import os
import logging
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import aiofiles

from database import init_db, save_user, get_user, issue_certificate_number
from certificate_generator import generate_certificate

# ======================
# НАСТРОЙКИ (вставь свои позже)
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8568411350:AAFqi-q5VcVZLXdvzFLZE8nzmoHTrfCFDXw")
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your_prodamos_secret_here")
BASE_URL = os.getenv("BASE_URL", "https://my-cert-bot.up.railway.app")  # будет изменён в Railway
PRODAMUS_OFFER_ID = os.getenv("PRODAMUS_OFFER_ID", "12345")  # позже укажешь свой

# ======================
# Логика FSM (шаги диалога)
# ======================
class UserStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_amount = State()

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
# Telegram-команды
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
    await state.update_data(full_name=full_name)
    await message.answer("💰 Теперь введите сумму сертификата в рублях (например, 1500):")
    await state.set_state(UserStates.waiting_for_amount)

@router.message(UserStates.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount < 100:
            await message.answer("Минимальная сумма — 100 рублей. Введите сумму:")
            return
    except ValueError:
        await message.answer("Введите число (например, 1500):")
        return

    data = await state.get_data()
    full_name = data["full_name"]
    user_id = message.from_user.id

    # Сохраняем в БД
    await save_user(user_id, full_name, amount)

    # ФОРМИРУЕМ ССЫЛКУ НА ОПЛАТУ (пока заглушка)
    pay_link = f"https://ваш-магазин.prodammus.ru/pay?offer_ids[]={PRODAMUS_OFFER_ID}&price={amount}&client_id={user_id}"

    await message.answer(
        f"Отлично! Ваш сертификат на {amount:,} ₽ готов к оплате.\n\n"
        f"Нажмите кнопку ниже, чтобы оплатить:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Оплатить сейчас", url=pay_link)]
            ]
        )
    )
    await state.clear()

# ======================
# Webhook от Продамуса (пока отключён, но подготовлен)
# ======================
@app.post(WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    # Позже сюда добавим проверку подписи и выдачу сертификата
    body = await request.json()
    client_id = body.get("client_id")
    if not client_id:
        return Response(status_code=400)

    user_id = int(client_id)
    user = await get_user(user_id)
    if not user or user["paid"]:
        return Response(status_code=200)

    # Выдаём номер и генерируем PDF
    cert_number = await issue_certificate_number(user_id)
    pdf_bytes = generate_certificate(user["full_name"], user["amount"], cert_number)

    # Сохраняем PDF во временный файл
    filename = f"cert_{cert_number}.pdf"
    async with aiofiles.open(filename, "wb") as f:
        await f.write(pdf_bytes)

    # Отправляем пользователю
    await bot.send_document(user_id, FSInputFile(filename))

    # Удаляем файл
    os.remove(filename)

    return JSONResponse({"status": "ok"})

# ======================
# Установка webhook при запуске
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    webhook_url = f"{BASE_URL}{WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"Webhook установлен: {webhook_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()

# ======================
# Запуск сервера
# ======================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
