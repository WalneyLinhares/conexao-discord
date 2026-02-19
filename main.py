import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
import pytz
import aiohttp
import uvicorn

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import discord
from discord.ext import commands, tasks
from discord import ui

from dotenv import load_dotenv

# -------------------------
# CONFIG / VARIÁVEIS DE AMBIENTE
# -------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
try:
    CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
except ValueError:
    CHANNEL_ID = 0

PORT = int(os.getenv("PORT", "8000"))
API_KEY = os.getenv("API_KEY")
WAKE_URL = os.getenv("WAKE_URL")
UPDATE_INTERVAL = int(os.getenv("UPDATE_INTERVAL", "180"))
RATE_LIMIT_DEF = os.getenv("RATE_LIMIT", "5/minute")
MESSAGE_ID_FILE = os.getenv("MESSAGE_ID_FILE", "./message_id.txt")
WAKE_INTERVAL = int(os.getenv("WAKE_INTERVAL_SECONDS", "300"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

ROOM_LINK = "https://www.habblet.city/room/6065930"
VIP_URL = "https://discord.com/channels/1186736897544945828/1211844747241586748"
THUMBNAIL_URL = "https://cdn.discordapp.com/attachments/1303772458762895480/1447735970358231143/Material_wave_loading.gif"
SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")

# -------------------------
# LOGGING
# -------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("bot-pro")

# -------------------------
# ESTADO GLOBAL
# -------------------------
MESSAGE_ID: Optional[int] = None 
LAST_UPDATE: float = 0 
PENDING_DATA: Optional[dict] = None 
PENDING_LOCK = asyncio.Lock() 
MESSAGE_ID_LOCK = asyncio.Lock() 
BACKOFF_STATE = {"wait_until": 0.0}

# -------------------------
# CONFIGURAÇÃO DO BOT DO DISCORD
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, case_insensitive=True)

# Atributo iniciado sem anotação direta para evitar erro de Type Annotation em execução
bot.http_session = None

# -------------------------
# FASTAPI + SLOWAPI
# -------------------------
limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return PlainTextResponse("Too Many Requests", status_code=429)

def _save_message_id(path: str, message_id: Optional[int]):
    try:
        if message_id is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "w") as f:
                f.write(str(message_id))
    except Exception as e:
        logger.warning("Não foi possível salvar o message_id: %s", e)

def _load_message_id(path: str) -> Optional[int]:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                txt = f.read().strip()
                if txt:
                    return int(txt)
    except Exception as e:
        logger.warning("Não foi possível carregar o message_id: %s", e)
    return None

# -------------------------
# ROTAS FASTAPI
# -------------------------
@app.post("/update-room")
@limiter.limit(RATE_LIMIT_DEF)
async def update_room(request: Request):
    global PENDING_DATA

    key = request.headers.get("x-api-key")
    if API_KEY and key != API_KEY:
        logger.warning("Chamada não autorizada a /update-room (API key inválida).")
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    room_name = body.get("roomName")
    user_count = body.get("userCount")

    if not room_name or user_count is None:
        raise HTTPException(status_code=400, detail="Invalid data")

    async with PENDING_LOCK:
        PENDING_DATA = {
            "room_name": str(room_name), 
            "user_count": int(user_count), 
            "received_at": datetime.now(timezone.utc).isoformat()
        }
        logger.debug("PENDING_DATA atualizado: %s", PENDING_DATA)

    return {"status": "ok"}

@app.api_route("/wake", methods=["GET", "POST"])
async def wake():
    logger.info("[FASTAPI] /wake chamado")
    return {"status": "alive"}

# -------------------------
# COMPONENTES / VIEW DO DISCORD
# -------------------------
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
            ui.TextDisplay(content=f"```fix\n{ROOM_LINK}\n```"),
            accessory=ui.Button(style=discord.ButtonStyle.link, url=ROOM_LINK, label="Entrar")
        )
        container.add_item(section_room_link)

        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(content="🔗 Links rápidos"))
        container.add_item(ui.ActionRow(ui.Button(style=discord.ButtonStyle.link, url=VIP_URL, label="💎 VIP")))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(content=f"*🕒 {current_time}*"))

        self.add_item(container)

# -------------------------
# AUXILIARES DE SEGURANÇA / CONTROLE
# -------------------------
async def _get_or_create_message(channel: discord.TextChannel) -> discord.Message:
    global MESSAGE_ID

    async with MESSAGE_ID_LOCK:
        if MESSAGE_ID:
            try:
                msg = await channel.fetch_message(MESSAGE_ID)
                return msg
            except discord.NotFound:
                logger.info("MESSAGE_ID salvo não encontrado. Criando novo.")
                MESSAGE_ID = None
            except discord.HTTPException as e:
                logger.warning("Erro em fetch_message: %s", e)

        placeholder = await channel.send("🔄 Painel inicializando…")
        MESSAGE_ID = placeholder.id
        _save_message_id(MESSAGE_ID_FILE, MESSAGE_ID)
        logger.info("Criado novo placeholder message id=%s", MESSAGE_ID)
        return placeholder

def _is_in_backoff() -> bool:
    now = asyncio.get_event_loop().time()
    return now < BACKOFF_STATE.get("wait_until", 0.0)

def _set_backoff(seconds: float):
    now = asyncio.get_event_loop().time()
    BACKOFF_STATE["wait_until"] = now + seconds
    logger.warning("Backoff ativado por %.1f segundos", seconds)

# -------------------------
# LOOP SEGURO DE ATUALIZAÇÃO
# -------------------------
@tasks.loop(seconds=5)
async def update_components_periodically():
    global LAST_UPDATE, PENDING_DATA

    await bot.wait_until_ready()

    if _is_in_backoff():
        return

    async with PENDING_LOCK:
        to_process = PENDING_DATA

    if not to_process:
        return

    now = datetime.now().timestamp()
    if now - LAST_UPDATE < UPDATE_INTERVAL:
        return

    try:
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            logger.warning("Canal %s não encontrado.", CHANNEL_ID)
            return

        msg = await _get_or_create_message(channel)

        room_name = to_process["room_name"]
        user_count = int(to_process["user_count"])
        current_time = datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y - %H:%M')

        view = RoomStatusView(room_name, user_count, current_time)

        max_attempts = 4
        attempt = 0
        backoff_seconds = 1.0

        while attempt < max_attempts:
            attempt += 1
            try:
                await msg.edit(view=view, content=None)
                LAST_UPDATE = datetime.now().timestamp()
                async with PENDING_LOCK:
                    PENDING_DATA = None
                logger.info("LayoutView atualizada com sucesso (tentativa %d)", attempt)
                break

            except discord.HTTPException as e:
                text = str(e)
                if "429" in text or "Cloudflare" in text or "rate limit" in text.lower():
                    logger.warning("429 detectado na tentativa %d: %s", attempt, text[:200])
                    _set_backoff(backoff_seconds * 2)
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= 2
                    continue
                else:
                    logger.exception("HTTPException ao editar mensagem: %s", e)
                    break

            except Exception as e:
                logger.exception("Erro inesperado ao editar mensagem: %s", e)
                break

        else:
            logger.error("Falhou após %d tentativas. Backoff maior aplicado.", max_attempts)
            _set_backoff(60)

    except Exception as e:
        logger.exception("Erro fatal no update_components_periodically: %s", e)

# -------------------------
# PING DE HORÁRIO
# -------------------------
# @tasks.loop(hours=1)
# async def hourly_everyone_ping():
#     await bot.wait_until_ready()
#     try:
#         channel = bot.get_channel(CHANNEL_ID)
#         if channel is None:
#             return
#         msg = await channel.send("@everyone ⏰ Atualização automática feita.")
#         await asyncio.sleep(1)
#         await msg.delete()
#         logger.info("Ping horário enviado e apagado.")
#     except Exception as e:
#         logger.exception("Erro no hourly ping: %s", e)

# -------------------------
# LOOP DE WAKE (MANTÉM OUTRO SERVIDOR ACORDADO)
# -------------------------
@tasks.loop(seconds=WAKE_INTERVAL)
async def ping_render_wake():
    await bot.wait_until_ready()
    if not WAKE_URL:
        return
    if bot.http_session is None:
        bot.http_session = aiohttp.ClientSession()
    try:
        async with bot.http_session.get(WAKE_URL, timeout=10) as resp:
            logger.debug("[WAKE] status=%s", resp.status)
            if resp.status >= 400:
                text = await resp.text()
                snippet = text[:300].replace("\n", " ")
                logger.warning("[WAKE] status=%s body=%s", resp.status, snippet)
    except Exception as e:
        logger.warning("[WAKE ERRO] %s", e)

# -------------------------
# EVENTOS DO DISCORD
# -------------------------
@bot.event
async def on_ready():
    logger.info("Bot conectado: %s (id=%s)", bot.user, bot.user.id)

    global MESSAGE_ID
    mid = _load_message_id(MESSAGE_ID_FILE)
    if mid:
        MESSAGE_ID = mid
        logger.info("MESSAGE_ID carregado: %s", MESSAGE_ID)

    if bot.http_session is None:
        bot.http_session = aiohttp.ClientSession()

    if not update_components_periodically.is_running():
        update_components_periodically.start()
    # if not hourly_everyone_ping.is_running():
    #     hourly_everyone_ping.start()
    if not ping_render_wake.is_running():
        ping_render_wake.start()

    logger.info("Tarefas em background iniciadas.")

@bot.event
async def on_disconnect():
    logger.warning("Bot desconectado.")

async def _close_bot_resources():
    if bot.http_session:
        try:
            await bot.http_session.close()
            logger.info("Sessão aiohttp fechada")
        except Exception as e:
            logger.warning("Erro ao fechar sessão aiohttp: %s", e)

# -------------------------
# EXECUÇÃO: FASTAPI + BOT
# -------------------------
async def _run_uvicorn():
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    if not TOKEN or CHANNEL_ID == 0:
        logger.critical("DISCORD_TOKEN ou CHANNEL_ID ausentes.")
        return

    discord_task = asyncio.create_task(bot.start(TOKEN))
    api_task = asyncio.create_task(_run_uvicorn())

    done, pending = await asyncio.wait([discord_task, api_task], return_when=asyncio.FIRST_EXCEPTION)
    for t in pending:
        t.cancel()

    await _close_bot_resources()

if __name__ == "__main__":
    try:
        async def start():
            async with bot:
                await main()

        asyncio.run(start())
    except KeyboardInterrupt:
        logger.info("Encerramento solicitado pelo usuário.")
    except Exception as e:
        logger.exception("Main crashou: %s", e)
    finally:
        try:
            _save_message_id(MESSAGE_ID_FILE, MESSAGE_ID)
        except Exception:
            pass