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
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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

render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# FSM States
# ======================
class UserStates(StatesGroup):
    waiting_for_name = State()

# ======================
# FastAPI + Aiogram
# ======================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

app = FastAPI()

# ======================
# Продамус webhook модель
# ======================
class ProdamosWebhookData(BaseModel):
    client_id: str  # передаётся в ссылке как ?client_id=123

# ======================
# Обработчики Telegram
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

    # Используем твою ссылку + demo_mode=1
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
# Просмотр базы
# ======================
@router.message(Command("listusers"))
async def list_users(message: Message):
    if message.from_user.id != 8568411350:  # ← ЗАМЕНИ НА СВОЙ USER ID
        await message.answer("❌ Доступ запрещён")
        return

    async with aiosqlite.connect("users.db") as db:
        async with db.
