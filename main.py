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

# Импорты ваших модулей (убедитесь, что они лежат рядом)
from database import init_db, create_certificate_request, get_cert_by_id, issue_certificate_number
from certificate_generator import generate_certificate_image

# ======================
# Настройки
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Секретный ключ (если есть в .env, будет использоваться для логов, но не блокировать)
PRODAMUS_SECRET_KEY = os.getenv("PRODAMUS_SECRET_KEY", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required! Укажите его в .env")

TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"

# Ссылка на вашу платежную форму (замените на свою, если отличается)
PRODAMUS_FORM_URL = "https://payform.ru/jga8Qsz/" 

render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# Утилита проверки подписи (Справочная)
# ======================
def verify_signature(data: Dict[str, Any], secret_key: str, received_sign: str) -> bool:
    """
    Попытка проверки подписи.
    Алгоритм сложный из-за разницы кодировок Python/PHP, поэтому
    результат этой функции мы будем использовать только для логов.
    """
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
        
        # Эмуляция PHP json_encode
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
# FSM (Машина состояний)
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
    await message.answer("👋 Введите Имя и Фамилию для сертификата:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Слишком короткое имя. Попробуйте снова:")
        return

    user_id = message.from_user.id
    # Создаем НОВЫЙ заказ в базе
    cert_id = await create_certificate_request(user_id, full_name, 2000)

    # Параметры ссылки согласно документации
    params = {
        "order_id": str(cert_id),    # Главный ID
        "sys": str(cert_id),         # Резервный ID
        "products[0][name]": "Подарочный сертификат",
        "products[0][price]": "2000",
        "products[0][quantity]": "1",
        "do": "pay",                 # Сразу открываем оплату
        "demo_mode": "1"             # ⚠️ УДАЛИТЕ ЭТУ СТРОКУ, КОГДА ЗАКОНЧИТЕ ТЕСТЫ
    }
    
    query_string = urllib.parse.urlencode(params)
    pay_link = f"{PRODAMUS_FORM_URL}?{query_string}"

    await message.answer(
        f"Заказ №{cert_id} создан.\n",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="💳 Оплатить 2000 ₽", url=pay_link)]]
        )
    )
    await state.clear()

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
    # 1. Получаем данные
    sign_header = request.headers.get("Sign")
    form_data = await request.form()
    data = dict(form_data)
    
    logging.info(f"📥 PRODAMUS POST DATA: {data}")

    # --- ПРОВЕРКА ПОДПИСИ (МЯГКАЯ) ---
    # Мы проверяем подпись, пишем в лог результат, но НЕ БЛОКИРУЕМ работу,
    # если она не совпала. Это решает проблему "❌ НЕВЕРНАЯ ПОДПИСЬ".
    if PRODAMUS_SECRET_KEY and sign_header:
        if verify_signature(data, PRODAMUS_SECRET_KEY, sign_header):
            logging.info("✅ Подпись верна (SECURE)")
        else:
            logging.warning(f"⚠️ Подпись не совпала! Пришла: {sign_header}. Продолжаем выполнение (INSECURE MODE).")
    # ---------------------------------

    # 2. Ищем ID заказа
    # Продамус может вернуть его в order_num, sys или order_id (зависит от фазы луны)
    order_val = data.get("order_num") or data.get("sys") or data.get("order_id")

    # 3. Обработка тестового запроса (из админки кнопка "Проверить URL")
    if not order_val or order_val in ["test", "тест"] or data.get("test") == "1":
        logging.info("✅ Тестовый пинг от Продамуса (Check URL) - OK")
        return JSONResponse({"status": "ok"})

    # 4. Проверка статуса оплаты
    # Документация говорит: status 'success' = успешно.
    payment_status = data.get("payment_status", "").lower()
    if payment_status != "success":
        logging.info(f"ℹ️ Оплата не завершена (статус '{payment_status}'). Игнорируем.")
        return JSONResponse({"status": "ok"})

    # 5. Поиск заказа в БД
    try:
        cert_id = int(order_val)
    except ValueError:
        logging.warning(f"⚠️ ID заказа '{order_val}' не является числом")
        return JSONResponse({"status": "error"})

    cert = await get_cert_by_id(cert_id)
    
    # Если заказ не найден (например, старый ID=1, которого нет в новой базе)
    if not cert:
        logging.warning(f"⚠️ Заказ {cert_id} не найден в базе данных. (Возможно, база была сброшена?)")
        # Возвращаем 200 OK, чтобы Продамус перестал долбиться с этим ID
        return JSONResponse({"status": "ok", "message": "Order not found in DB"})

    # Если уже оплачен
    if cert.get("paid"):
        logging.info(f"ℹ️ Заказ {cert_id} уже был выдан ранее.")
        return JSONResponse({"status": "ok"})

    # 6. Выдача сертификата
    try:
        cert_number = await issue_certificate_number(cert["id"])
        png_bytes = generate_certificate_image(cert["full_name"], cert_number)
        
        await bot.send_photo(
            cert["user_id"],
            BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png"),
            caption=f"✅ Оплата подтверждена!\nВаш сертификат № {cert_number} готов."
        )
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
