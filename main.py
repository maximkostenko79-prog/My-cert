# main.py
import os
import logging
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Update
from fastapi import FastAPI, Request, Response, Form
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import aiosqlite

from database import init_db, create_certificate_request, get_cert_by_id, issue_certificate_number
from certificate_generator import generate_certificate_image

# ======================
# Настройки
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required!")

TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"

# 🔑 ЖЁСТКО УКАЗЫВАЕМ BASE_URL (замените на ваш реальный URL!)
BASE_URL = "https://my-cert.onrender.com"

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
# Telegram handlers
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

    pay_link = f"https://payform.ru/jga8Qsz/?client_id={cert_id}&demo_mode=1"

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
# Тестовая команда
# ======================
@router.message(Command("testcert"))
async def test_certificate(message: Message):
    user_id = message.from_user.id
    full_name = "Максим Костенко"
    cert_id = await create_certificate_request(user_id, full_name, 2000)
    cert_number = await issue_certificate_number(cert_id)
    png_bytes = generate_certificate_image(full_name, cert_number)

    await message.answer("✅ Тестовый сертификат готов!")
    await bot.send_photo(
        user_id,
        BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png")
    )

# ======================
# Просмотр базы (только для вас)
# ======================
@router.message(Command("listusers"))
async def list_users(message: Message):
    # 🔐 ЗАМЕНИТЕ НА СВОЙ TELEGRAM USER ID
    if message.from_user.id != 8568411350:
        await message.answer("❌ Доступ запрещён")
        return

    try:
        async with aiosqlite.connect("users.db") as db:
            async with db.execute("SELECT id, user_id, full_name, cert_number, paid FROM certificates") as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"Ошибка базы: {e}")
        return

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
    try:
        update = await request.json()
        await dp.feed_update(bot, Update(**update))
        return {"ok": True}
    except Exception as e:
        logging.error(f"Ошибка Telegram webhook: {e}")
        return {"ok": False}

# 🔑 ОБРАБОТЧИК PRODAMUS — принимает form data
@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(customer_extra: str = Form(...)):
    logging.info(f"📥 Продамус: customer_extra = '{customer_extra}'")

    # Проверка: если это тест — игнорируем
    if customer_extra == "test":
        logging.info("ℹ️ Тестовый запрос от Продамуса — пропускаем")
        return JSONResponse({"status": "ok", "message": "test ignored"})

    try:
        cert_id = int(customer_extra)
    except ValueError:
        logging.warning(f"⚠️ '{customer_extra}' не является числом")
        return Response(status_code=400)

    cert = await get_cert_by_id(cert_id)
    if not cert:
        logging.warning(f"⚠️ Сертификат {cert_id} не найден")
        return Response(status_code=404)

    cert_number = await issue_certificate_number(cert["id"])
    png_bytes = generate_certificate_image(cert["full_name"], cert_number)

    await bot.send_photo(
        cert["user_id"],
        BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png")
    )

    logging.info(f"✅ Сертификат №{cert_number} отправлен пользователю {cert['user_id']}")
    return JSONResponse({"status": "ok"})

# Заглушка для GET (для тестирования в браузере)
@app.get(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook_get():
    return {"status": "ok", "note": "Use POST from Prodamos"}

# ======================
# Startup / Shutdown
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    webhook_url = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    try:
        await bot.set_webhook(url=webhook_url)
        logging.info(f"✅ Telegram webhook установлен: {webhook_url}")
    except Exception as e:
        logging.error(f"❌ Ошибка установки webhook: {e}")

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
