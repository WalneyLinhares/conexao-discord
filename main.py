import os
import asyncio
from datetime import datetime
import pytz
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from dotenv import load_dotenv
import requests

# --- Importações DISCORD.PY E UI ---
import discord
from discord.ext import commands, tasks
from discord import ui 
# ------------------------------------

# ==========================================================
# 1. VARIÁVEIS DE AMBIENTE E CONFIGURAÇÕES
# ==========================================================
load_dotenv()

TOKEN: str = os.getenv("DISCORD_TOKEN")
try:
    CHANNEL_ID: int = int(os.getenv("CHANNEL_ID"))
except (TypeError, ValueError):
    CHANNEL_ID: int = 0
    
PORT: int = int(os.getenv("PORT", 8000))
API_KEY: str = os.getenv("API_KEY")

ROOM_LINK = "https://www.habblet.city/room/6065930"
UPDATE_INTERVAL = 180

MESSAGE_ID: int | None = None 
LAST_UPDATE: float = 0
PENDING_DATA: dict | None = None
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')
VIP_URL = "https://discord.com/channels/1186736897544945828/1211844747241586748"
THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1303772458762895480/1447735970358231143/Material_wave_loading.gif"


WAKE_URL = os.getenv("WAKE_URL")


# ==========================================================
# 2. CONFIGURAÇÃO DO BOT DISCORD.PY
# ==========================================================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents) 


# ==========================================================
# 3. CONSTRUTOR DE COMPONENTS V2
# ==========================================================
class RoomStatusView(ui.LayoutView):
    def __init__(self, room_name: str, user_count: int, current_time: str):
        super().__init__(timeout=None)

        container = ui.Container()

        section_top = ui.Section(
            ui.TextDisplay(content=f"{room_name.upper()}"),
            ui.TextDisplay(content=f"```🎮 Usuários no quarto: {user_count}```"),
            accessory=ui.Thumbnail(media=THUMBNAIL_URL),
        )
        container.add_item(section_top)

        section_room_link = ui.Section(
            ui.TextDisplay(
                content=f"```fix\n{ROOM_LINK}\n```"
            ),
            accessory=ui.Button(
                style=discord.ButtonStyle.link,
                url=ROOM_LINK,
                label="Entrar",
            )
        )
        container.add_item(section_room_link)

        container.add_item(ui.Separator())

        container.add_item(
            ui.TextDisplay(content="🔗 Links rápidos")
        )

        container.add_item(
            ui.ActionRow(
                ui.Button(
                    style=discord.ButtonStyle.link,
                    url=VIP_URL,
                    label="💎 VIP",
                )
            )
        )

        container.add_item(ui.Separator())

        container.add_item(
            ui.TextDisplay(content=f"*🕒 {current_time}*")  
        )

        self.add_item(container)


# ==========================================================
# 4. LOOP DE ATUALIZAÇÃO
# ==========================================================
@tasks.loop(seconds=5) 
async def update_components_periodically():
    global MESSAGE_ID, LAST_UPDATE, PENDING_DATA
    
    await bot.wait_until_ready()
    channel: discord.TextChannel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        return

    if MESSAGE_ID is None:
        try:
            async for msg in channel.history(limit=20):
                if msg.author.id == bot.user.id:
                    MESSAGE_ID = msg.id
                    break
        except Exception:
            pass
             
    now = datetime.now().timestamp()
    
    if PENDING_DATA and (now - LAST_UPDATE >= UPDATE_INTERVAL):
        try:
            room_name = PENDING_DATA["room_name"]
            user_count = PENDING_DATA["user_count"]
            current_time = datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y - %H:%M')
            
            view = RoomStatusView(room_name, user_count, current_time)
            
            if MESSAGE_ID:
                msg = await channel.fetch_message(MESSAGE_ID)
                await msg.edit(view=view, content=None, embed=None)
                print("[BOT] LayoutView atualizada.")
            else:
                msg = await channel.send(view=view, content=None, embed=None)
                MESSAGE_ID = msg.id
                print("[BOT] Nova mensagem LayoutView enviada.")
            
            LAST_UPDATE = now
            PENDING_DATA = None 

        except discord.NotFound:
            MESSAGE_ID = None
            print("[AVISO] Mensagem original deletada. Tentando enviar nova.")
        except Exception as e:
            print(f"[ERRO FATAL] ao atualizar Mensagem Discord: {e}")


# ==========================================================
# 4.1 LOOP — AVISO COM @EVERYONE A CADA 1 HORA
# ==========================================================
@tasks.loop(hours=1)
async def hourly_everyone_ping():
    await bot.wait_until_ready()

    channel: discord.TextChannel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return

    try:
        msg = await channel.send("@everyone ⏰ Atualização automática feita.")
        await asyncio.sleep(1)
        await msg.delete()
        print("[BOT] Aviso horário enviado e apagado.")
    except Exception as e:
        print(f"[ERRO HOURLY] {e}")


# ==========================================================
# 🔥 4.2 — LOOP QUE PINGA O RENDER AUTOMATICAMENTE
# ==========================================================
@tasks.loop(minutes=5)
async def ping_render_wake():
    await bot.wait_until_ready()

    if not WAKE_URL:
        print("[WAKE] Nenhuma WAKE_URL configurada.")
        return

    try:
        r = requests.get(WAKE_URL, timeout=5)
        print(f"[WAKE] Ping enviado {r.text}")
    except Exception as e:
        print(f"[WAKE ERRO] {e}")


# ==========================================================
# EVENTO ON_READY
# ==========================================================
@bot.event
async def on_ready():
    print(f"Bot {bot.user} conectado e pronto. ID: {bot.user.id}")
    
    if not update_components_periodically.is_running():
        update_components_periodically.start()

    if not hourly_everyone_ping.is_running():
        hourly_everyone_ping.start()

    if not ping_render_wake.is_running():
        ping_render_wake.start()


# ==========================================================
# 5. FASTAPI + ENDPOINT
# ==========================================================
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/update-room")
async def update_room(request: Request):
    global PENDING_DATA, LAST_UPDATE
    key = request.headers.get("x-api-key")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await request.json()
    room_name = data.get("roomName")
    user_count = data.get("userCount")

    if not room_name or user_count is None:
        raise HTTPException(status_code=400, detail="Invalid data")

    PENDING_DATA = {"room_name": room_name, "user_count": user_count}
    LAST_UPDATE = 0

    print("[API] Dados recebidos:", PENDING_DATA)
    return {"status": "ok"}


# ==========================================================
# 6. EXECUÇÃO
# ==========================================================
async def run_discord_bot():
    try:
        await bot.start(TOKEN)
    except Exception as e:
        print(f"Erro ao iniciar o bot Discord.py: {e}")

async def run_fastapi_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    if not TOKEN or CHANNEL_ID == 0 or not API_KEY:
        print("ERRO CRÍTICO: Configure as variáveis de ambiente.")
        return

    discord_task = asyncio.create_task(run_discord_bot())
    fastapi_task = asyncio.create_task(run_fastapi_server())
    
    await asyncio.gather(discord_task, fastapi_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServidor e Bot desligados pelo usuário.")
