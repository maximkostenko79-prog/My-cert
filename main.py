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

# Импорты ваших модулей
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

# Определяем URL хоста
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# FSM States
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
# Telegram Handlers
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

    # 🔥 ИСПРАВЛЕНО: убраны пробелы вокруг {cert_id}
    pay_link = f"https://payform.ru/jga8Qsz/?order_id={cert_id}&demo_mode=1"

    await message.answer(
        f"Отлично! Сертификат для {full_name} создан.\n"
        "Для получения нажмите кнопку оплаты ниже:",
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
    full_name = "Тестовый Покупатель"
    cert_id = await create_certificate_request(user_id, full_name, 2000)
    cert_number = await issue_certificate_number(cert_id)
    png_bytes = generate_certificate_image(full_name, cert_number)

    await message.answer("✅ Тестовый сертификат готов!")
    await bot.send_photo(
        user_id,
        BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png")
    )

# ======================
# Просмотр базы (только для админа)
# ======================
@router.message(Command("listusers"))
async def list_users(message: Message):
    ADMIN_ID = 8568411350  # ← ЗАМЕНИТЕ НА СВОЙ ID
    
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Доступ запрещён")
        return

    try:
        async with aiosqlite.connect("users.db") as db:
            async with db.execute("SELECT id, user_id, full_name, cert_number, paid FROM certificates ORDER BY id DESC LIMIT 10") as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        await message.answer(f"Ошибка БД: {e}")
        return

    if not rows:
        await message.answer("База данных пуста.")
        return

    text = "📋 Последние 10 заявок:\n\n"
    for row in rows:
        cert_id, user_id, name, cert, paid = row
        status = "✅ ОПЛАЧЕНО" if paid else "⏳ Ждет оплаты"
        cert_num_str = cert if cert else "—"
        text += f"ID: {cert_id} | {status}\n👤 {name}\n📄 №: {cert_num_str}\n\n"

    await message.answer(text)

# ======================
# Webhooks
# ======================
@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        await dp.feed_update(bot, Update(**update))
    except Exception as e:
        logging.error(f"Ошибка в Telegram webhook: {e}")
    return {"ok": True}

@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(order_num: str = Form(...)):
    logging.info(f"📥 Получен webhook от Продамуса. order_num={order_num}")

    # Обработка тестового запроса
    if order_num in ("test", "тест"):
        logging.info("✅ Тестовый запрос — OK")
        return JSONResponse({"status": "ok", "message": "Test received"})

    # Валидация ID
    try:
        cert_id = int(order_num)
    except ValueError:
        logging.warning(f"⚠️ Неверный order_num: '{order_num}'")
        return JSONResponse({"status": "error", "message": "Invalid order_num"})

    # Поиск сертификата
    cert = await get_cert_by_id(cert_id)
    if not cert:
        logging.warning(f"⚠️ Сертификат {cert_id} не найден")
        return JSONResponse({"status": "error", "message": "Certificate not found"})

    # Выдача сертификата
    try:
        cert_number = await issue_certificate_number(cert["id"])
        png_bytes = generate_certificate_image(cert["full_name"], cert_number)

        await bot.send_photo(
            cert["user_id"],
            BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png"),
            caption=f"🎉 Поздравляем! Оплата прошла успешно.\nВаш сертификат № {cert_number} готов."
        )
        logging.info(f"✅ Сертификат №{cert_number} отправлен {cert['user_id']}")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logging.error(f"❌ Ошибка выдачи: {e}")
        return Response(status_code=500)

@app.get(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook_get():
    return {"status": "ok", "message": "Use POST"}

# ======================
# Startup / Shutdown
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    webhook_url = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"🚀 Бот запущен. Webhook: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    # await bot.session.close()  # ← не нужно в aiogram 3.x

# ======================
# Запуск
# ======================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
