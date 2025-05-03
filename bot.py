import os
import re
import logging
from aiohttp import web
import emoji
from prompts import SYSTEM_PROMPT
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from dotenv import load_dotenv
from openai import OpenAI

# Разрешённые эмодзи — запретить все
ALLOWED_EMOJIS = set()

def strip_disallowed_emojis(text: str, allowed: set) -> str:
    emojis_in_text = {e['emoji'] for e in emoji.emoji_list(text)}
    for emj in emojis_in_text:
        if emj not in allowed:
            text = text.replace(emj, '')
    return text

# прочитаем .env в os.environ
load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# В .env должны быть такие строки (без кавычек):
# TELEGRAM_BOT_TOKEN=7291191380:AAE...75aIom4
# OPENAI_API_KEY=sk-proj-pJqD...
# OPENAI_BASE_URL=https://hubai.loe.gg/v1

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY     = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL    = os.getenv("OPENAI_BASE_URL")

if not TELEGRAM_BOT_TOKEN or not OPENAI_API_KEY:
    logging.error("Не задано TELEGRAM_BOT_TOKEN или OPENAI_API_KEY в .env")
    exit(1)

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
MAX_LENGTH = 3800

# Обработка команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я — Мисс Минутка! Если не знаешь, с чего начать — просто напиши.")

# История сообщений пользователей
history = {}

# Обработка сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text or ""
    print("Получено сообщение от пользователя:", user_message)
    message_lower = user_message.lower()
    chat_id = update.effective_chat.id

    if chat_id not in history:
        history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    history[chat_id].append({"role": "user", "content": user_message})

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=history[chat_id],
            temperature=0.7
        )

        reply_text = completion.choices[0].message.content
        reply_text = strip_disallowed_emojis(reply_text, ALLOWED_EMOJIS)
        
        history[chat_id].append({"role": "assistant", "content": reply_text})
        history[chat_id] = history[chat_id][-10:]

        await update.message.reply_text(reply_text.strip())

    except Exception as e:
        logging.error(f"Ошибка при запросе к OpenAI: {e}")
        await update.message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз чуть позже.")

from telegram.error import Conflict

# --- Запуск Telegram-бота ---
async def run_telegram_bot():
    tg_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", start))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
    except Conflict:
        print("⚠️ Бот уже запущен где-то ещё. Завершаем.")

# --- HTTP-приложение для Render ---
async def health(request):
    return web.Response(text="Бот активен!")

web_app = web.Application()  # ← эта строка ОБЯЗАТЕЛЬНА
web_app.add_routes([web.get("/", health)])

@web_app.on_startup.append
async def on_startup(app):
    await run_telegram_bot()

app = web_app  # ← ЭТО экспортируется для Gunicorn