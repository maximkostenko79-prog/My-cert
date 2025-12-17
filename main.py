# main.py
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

from database import init_db, create_certificate_request, get_cert_by_id, issue_certificate_number
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

render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# FSM
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
# Основной сценарий
# ======================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("👋 Привет! Введите имя и фамилию получателя сертификата:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Имя слишком короткое. Попробуйте снова:")
        return

    user_id = message.from_user.id
    cert_id = await create_certificate_request(user_id, full_name, 2000)

    pay_link = f"https://ваш-магазин.prodammus.ru/pay?offer_ids[]={PRODAMUS_OFFER_ID}&price=2000&client_id={cert_id}"

    await message.answer(
        "Отлично! Ваш подарочный сертификат готов к оплате.\n\n"
        "Нажмите кнопку ниже:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Оплатить 2000 ₽", url=pay_link)]
            ]
        )
    )
    await state.clear()

# ======================
# Тестовая команда (мгновенная выдача)
# ======================
@router.message(Command("testcert"))
async def test_certificate(message: Message):
    user_id = message.from_user.id
    full_name = "Максим Костенко"
    cert_id = await create_certificate_request(user_id, full_name, 2000)
    cert_number = await issue_certificate_number(cert_id)
    png_bytes = generate_certificate_image(full_name, cert_number)

    from aiogram.types import BufferedInputFile

    await message.answer("✅ Тестовый сертификат готов!")
    await bot.send_photo(
    user_id,
    BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png")
)

# ======================
# Команда для просмотра базы
# ======================
@router.message(Command("listusers"))
async def list_users(message: Message):
    if message.from_user.id != 8568411350:  # ← ЗАМЕНИ НА СВОЙ USER ID
        await message.answer("❌ Доступ запрещён")
        return

    async with aiosqlite.connect("users.db") as db:
        async with db.execute("SELECT id, user_id, full_name, cert_number, paid FROM certificates") as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("База данных пуста.")
        return

    text = "📋 Сертификаты:\n\n"
    for row in rows:
        cert_id, user_id, name, cert, paid = row
        status = "✅" if paid else "⏳"
        text += f"{status} ID: `{cert_id}`\n   Получатель: {name}\n   №: {cert or '—'}\n\n"

    await message.answer(f"```{text}```", parse_mode="MarkdownV2")

# ======================
# Webhooks
# ======================
@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    update = await request.json()
    await dp.feed_update(bot, Update(**update))
    return {"ok": True}

@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    body = await request.json()
    client_id = body.get("client_id")
    if not client_id:
        return Response(status_code=400)

    try:
        cert_id = int(client_id)
    except ValueError:
        return Response(status_code=400)

    cert = await get_cert_by_id(cert_id)
    if not cert:
        return Response(status_code=404)

    cert_number = await issue_certificate_number(cert["id"])
    png_bytes = generate_certificate_image(cert["full_name"], cert_number)

    from aiogram.types import BufferedInputFile
    await bot.send_photo(
    cert["user_id"],
    BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png")
)

   

    return JSONResponse({"status": "ok"})

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
