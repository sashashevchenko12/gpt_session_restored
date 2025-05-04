
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)

MAX_LENGTH = 3800

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет, добро пожаловать в «Актёрскую сессию»! Я — Мисс Минутка 💫 Если не знаешь, с чего начать — просто напиши.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    if not user_message:
        return

    # Простой фильтр на темы вне проекта
    forbidden_topics = ["кошка", "собака,

 "бутерброд", "семья", "погода", "война", "любовь"]
    if any(topic in user_message.lower() for topic in forbidden_topics):
        await update.message.reply_text("🧭 Я могу говорить только о темах, связанных с проектом «Актёрская сессия»: уроки, практика, вдохновение и голос. Обо всём этом — с радостью!")
        return

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": user_message}],
            max_tokens=1500,
            temperature=0.7
        )
        full_reply = response.choices[0].message.content.strip()
        for chunk in [full_reply[i:i+MAX_LENGTH] for i in range(0, len(full_reply), MAX_LENGTH)]:
            await update.message.reply_text(chunk)
    except Exception as e:
        logger.error(f"GPT error: {e}")
        await update.message.reply_text("❌ Что-то пошло не так. Попробуй ещё раз чуть позже.")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    main()
