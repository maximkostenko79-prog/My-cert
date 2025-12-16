import os
import logging
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Update
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import aiofiles

from database import init_db, save_user, get_user, issue_certificate_number
from certificate_generator import generate_certificate

# ======================
# НАСТРОЙКИ
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required!")

PRODAMUS_OFFER_ID = os.getenv("PRODAMUS_OFFER_ID", "12345")

TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"

# Получаем URL от Render
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# FSM State
# ======================
class UserStates(StatesGroup):
    waiting_for_name = State()

# ======================
# Инициализация
# ======================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

app = FastAPI()

# ======================
# Основной сценарий: /start → имя → ссылка на оплату
# ======================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("👋 Привет! Введите ваше Имя и Фамилию:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Имя слишком короткое. Попробуйте снова:")
        return

    user_id = message.from_user.id
    await save_user(user_id, full_name, 2000)  # фиксированная сумма

    pay_link = f"https://ваш-магазин.prodammus.ru/pay?offer_ids[]={PRODAMUS_OFFER_ID}&price=2000&client_id={user_id}"

    await message.answer(
        "Отлично! Ваш подарочный сертификат на 2 000 ₽ готов к оплате.\n\n"
        "Нажмите кнопку ниже, чтобы оплатить:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Оплатить 2000 ₽", url=pay_link)]
            ]
        )
    )
    await state.clear()

# ======================
# ТЕСТОВАЯ КОМАНДА: мгновенная выдача сертификата
# ======================
@router.message(Command("testcert"))
async def test_certificate(message: Message):
    user_id = message.from_user.id
    full_name = "Максим Костенко"  # ← замени на своё имя для теста

    await save_user(user_id, full_name, 2000)
    cert_number = await issue_certificate_number(user_id)
    pdf_bytes = generate_certificate(full_name, cert_number)

    filename = f"cert_{cert_number}.pdf"
    async with aiofiles.open(filename, "wb") as f:
        await f.write(pdf_bytes)

    await message.answer("✅ Тестовый сертификат готов!")
    await bot.send_document(user_id, FSInputFile(filename))
    os.remove(filename)

# ======================
# Telegram webhook
# ======================
@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    update = await request.json()
    await dp.feed_update(bot, Update(**update))
    return {"ok": True}

# ======================
# Продамус webhook (временно не используется)
# ======================
@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    return JSONResponse({"status": "ok"})  # заглушка

# ======================
# Startup
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    webhook_url = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"✅ Webhook установлен: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()

# ======================
# Запуск
# ======================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
