import os
import asyncio
from datetime import datetime
import pytz
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import discord
from discord.ext import commands
import uvicorn
from dotenv import load_dotenv

# ------------------ CARREGAR VARIÁVEIS DE AMBIENTE ------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
PORT = int(os.getenv("PORT", 8000))
ROOM_LINK = "https://www.habblet.city/room/6065930"
GIF_URL = "https://cdn.discordapp.com/attachments/1303772458762895480/1424811285542863000/load-32.gif"
UPDATE_INTERVAL = 180
API_KEY = os.getenv("API_KEY")

MESSAGE_ID = None
LAST_UPDATE = 0
PENDING_DATA = None  # Guarda o último dado recebido

# ------------------ CONFIGURAÇÃO DO BOT ------------------
intents = discord.Intents.default()
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ CONFIGURAÇÃO FASTAPI ------------------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.post("/update-room")
async def update_room(request: Request):
    global PENDING_DATA
    key = request.headers.get("x-api-key")
    if key != API_KEY:
        return {"error": "Unauthorized"}, 401

    data = await request.json()
    room_name = data.get("roomName")
    user_count = data.get("userCount")
    if not room_name or user_count is None:
        return {"error": "Dados inválidos"}, 400

    PENDING_DATA = {"room_name": room_name, "user_count": user_count}
    print(PENDING_DATA)
    return {"status": "ok"}

# ------------------ FUNÇÃO DE ATUALIZAÇÃO DO EMBED ------------------
async def update_embed_periodically():
    global MESSAGE_ID, LAST_UPDATE, PENDING_DATA
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("Canal não encontrado!")
        return

    print(f"Iniciando loop de atualização no canal: {channel.name} ({CHANNEL_ID})")

    # Tentar encontrar a última mensagem do bot no canal
    try:
        messages = [m async for m in channel.history(limit=10)]
        msg = next((m for m in messages if m.author == bot.user), None)
        if msg:
            MESSAGE_ID = msg.id
            print(f"Mensagem existente encontrada: {MESSAGE_ID}")
        else:
            MESSAGE_ID = None
            print("Nenhuma mensagem existente encontrada, irá criar nova quando necessário.")
    except Exception as e:
        print("Erro ao buscar histórico de mensagens:", e)
        MESSAGE_ID = None

    while not bot.is_closed():
        now = datetime.now().timestamp()
        if PENDING_DATA and now - LAST_UPDATE >= UPDATE_INTERVAL:
            try:
                room_name = PENDING_DATA["room_name"]
                user_count = PENDING_DATA["user_count"]

                embed = discord.Embed(
                    title=f"{room_name.upper()}",
                    description="Chame seus amigos e vem jogar!"
                )
                embed.set_thumbnail(url=GIF_URL)
                embed.add_field(
                    name="",
                    value=f"```fix\n🎮 {user_count} Usuários no quarto\n```",
                    inline=False
                )
                embed.add_field(
                    name="",
                    value=f"```fix\n{ROOM_LINK}```",
                    inline=True
                )

                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    label="QUARTO",
                    url=ROOM_LINK,
                    style=discord.ButtonStyle.link
                ))
                view.add_item(discord.ui.Button(
                    label="💎 VIP",
                    url="https://discord.com/channels/1186736897544945828/1211844747241586748",
                    style=discord.ButtonStyle.link
                ))
                embed.set_footer(
                    text=f"🕔{datetime.now(pytz.timezone("America/Sao_Paulo")).strftime('%d/%m/%Y - %H:%M')}"
                )

                if MESSAGE_ID:
                    try:
                        msg = await channel.fetch_message(MESSAGE_ID)
                        await msg.edit(embed=embed, view=view)
                        print("Mensagem atualizada")
                    except discord.errors.NotFound:
                        msg = await channel.send(embed=embed, view=view)
                        MESSAGE_ID = msg.id
                        print("Mensagem anterior não encontrada. Nova mensagem enviada.")
                else:
                    msg = await channel.send(embed=embed, view=view)
                    MESSAGE_ID = msg.id
                    print(f"Mensagem enviada: {MESSAGE_ID}")

                LAST_UPDATE = now
            except Exception as e:
                print("Erro ao atualizar embed:", e)

        await asyncio.sleep(5)

# ------------------ FUNÇÃO PRINCIPAL ------------------
async def main():
    # Cria tasks do FastAPI e do bot
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve())
    update_task = asyncio.create_task(update_embed_periodically())

    # Inicia o bot
    await bot.start(TOKEN)

# ------------------ EXECUTAR ------------------
if __name__ == "__main__":
    asyncio.run(main())