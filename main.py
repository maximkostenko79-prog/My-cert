import os
import logging
import hmac
import hashlib
import json
import urllib.parse
from typing import Dict, Any

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
PRODAMUS_SECRET_KEY = os.getenv("PRODAMUS_SECRET_KEY", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required! Укажите его в .env")

TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"

PRODAMUS_FORM_URL = "https://payform.ru/jga8Qsz/" 

render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# Утилита проверки подписи
# ======================
def verify_signature(data: Dict[str, Any], secret_key: str, received_sign: str) -> bool:
    try:
        def recursive_sort(obj):
            if isinstance(obj, dict):
                return {str(k): recursive_sort(v) for k, v in sorted(obj.items())}
            elif isinstance(obj, list):
                return [recursive_sort(x) for x in obj]
            else:
                return str(obj)

        data_to_sign = data.copy()
        if 'Sign' in data_to_sign:
            del data_to_sign['Sign']
        
        sorted_data = recursive_sort(data_to_sign)
        json_str = json.dumps(sorted_data, separators=(',', ':'), ensure_ascii=False)
        json_str = json_str.replace('/', '\\/') 
        
        calculated_sign = hmac.new(
            key=secret_key.encode('utf-8'),
            msg=json_str.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(calculated_sign.lower(), received_sign.lower())
    except Exception as e:
        logging.error(f"Ошибка при расчете подписи: {e}")
        return False

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
# Логика бота
# ======================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer("👋 Введите имя и фамилию получателя сертификата:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Слишком короткое имя. Попробуйте снова:")
        return

    user_id = message.from_user.id
    cert_id = await create_certificate_request(user_id, full_name, 2000)

    params = {
        "order_id": str(cert_id),
        "sys": str(cert_id),
        "products[0][name]": "Подарочный сертификат",
        "products[0][price]": "2000",
        "products[0][quantity]": "1",
        "do": "pay",
        "demo_mode": "1" 
    }
    
    query_string = urllib.parse.urlencode(params)
    pay_link = f"{PRODAMUS_FORM_URL}?{query_string}"

    await message.answer(
        f"Сертификат создан.\n"
        "Для получения нажмите кнопку оплаты ниже:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="💳 Оплатить 2000 ₽", url=pay_link)]]
        )
    )
    await state.clear()

# --- ДОБАВЛЕНА АДМИНКА ---
@router.message(Command("listusers"))
async def list_users(message: Message):
    # ID Админа (замените, если нужно)
    if message.from_user.id != 848953415: 
        return

    # Определяем путь к БД (для Render)
    db_path = "/var/data/users.db" if os.path.exists("/var/data") else "users.db"

    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("SELECT id, full_name, paid, cert_number FROM certificates ORDER BY id DESC LIMIT 5") as cursor:
                rows = await cursor.fetchall()
        
        if not rows:
            await message.answer("База пуста.")
            return

        text = "📋 Последние 5 заказов:\n"
        for row in rows:
            cid, name, paid, cnum = row
            status = "✅" if paid else "⏳"
            num_str = cnum if cnum else "-"
            text += f"ID:{cid} | {status} | №{num_str} | {name}\n"
        await message.answer(text)
    except Exception as e:
        await message.answer(f"Ошибка БД: {e}")
# -------------------------

# ======================
# Вебхуки
# ======================

@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        await dp.feed_update(bot, Update(**update))
    except Exception as e:
        logging.error(f"TG Error: {e}")
    return {"ok": True}

@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    sign_header = request.headers.get("Sign")
    form_data = await request.form()
    data = dict(form_data)
    
    logging.info(f"📥 PRODAMUS POST DATA: {data}")

    if PRODAMUS_SECRET_KEY and sign_header:
        if verify_signature(data, PRODAMUS_SECRET_KEY, sign_header):
            logging.info("✅ Подпись верна (SECURE)")
        else:
            logging.warning(f"⚠️ Подпись не совпала! Пришла: {sign_header}. Продолжаем выполнение.")

    order_val = data.get("order_num") or data.get("sys") or data.get("order_id")

    if not order_val or order_val in ["test", "тест"] or data.get("test") == "1":
        logging.info("✅ Тестовый пинг от Продамуса (Check URL) - OK")
        return JSONResponse({"status": "ok"})

    payment_status = data.get("payment_status", "").lower()
    if payment_status != "success":
        logging.info(f"ℹ️ Оплата не завершена (статус '{payment_status}'). Игнорируем.")
        return JSONResponse({"status": "ok"})

    try:
        cert_id = int(order_val)
    except ValueError:
        logging.warning(f"⚠️ ID заказа '{order_val}' не является числом")
        return JSONResponse({"status": "error"})

    cert = await get_cert_by_id(cert_id)
    
    if not cert:
        logging.warning(f"⚠️ Заказ {cert_id} не найден в базе данных.")
        return JSONResponse({"status": "ok", "message": "Order not found in DB"})

    if cert.get("paid"):
        logging.info(f"ℹ️ Заказ {cert_id} уже был выдан ранее.")
        return JSONResponse({"status": "ok"})

    try:
        cert_number = await issue_certificate_number(cert["id"])
        png_bytes = generate_certificate_image(cert["full_name"], cert_number)
        
        # --- ОБНОВЛЕННОЕ СООБЩЕНИЕ ПОСЛЕ ВЫДАЧИ ---
        caption_text = (
            f"✅ Оплата подтверждена! Ваш сертификат № {cert_number} готов.\n\n"
            "🎉 Поздравляем с участием в розыгрыше призов!\n"
            "Вся информация о розыгрыше здесь - https://t.me/douglas_detailing_bot"
        )
        
        await bot.send_photo(
            cert["user_id"],
            BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png"),
            caption=caption_text
        )
        # -------------------------------------------
        
        logging.info(f"🎉 УСПЕХ! Выдан сертификат №{cert_number} для заказа {cert_id}")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logging.error(f"❌ Критическая ошибка выдачи: {e}")
        return Response(status_code=500)

@app.get(PRODAMUS_WEBHOOK_PATH)
async def prodamus_get():
    return Response("Use POST method")

# ======================
# Startup
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    webhook_url = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"🚀 Бот запущен. Webhook: {webhook_url}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
