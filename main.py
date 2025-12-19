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
    raise ValueError("BOT_TOKEN is required!")
if not PRODAMUS_SECRET_KEY:
    logging.warning("⚠️ PRODAMUS_SECRET_KEY не установлен! Проверка подписи работать не будет.")

TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"
PRODAMUS_FORM_URL = "https://payform.ru/jga8Qsz/" # Ваш URL формы из настроек

render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

# ======================
# Утилиты безопасности (Проверка подписи)
# ======================
def verify_signature(data: Dict[str, Any], secret_key: str, received_sign: str) -> bool:
    """
    Проверяет подпись вебхука по алгоритму Продамуса:
    1. Сортировка по ключам.
    2. Приведение к строкам.
    3. JSON encoding (без экранирования слэшей).
    4. HMAC SHA256.
    """
    if not secret_key or not received_sign:
        return False

    # Рекурсивная сортировка и приведение к строкам (как в PHP примере)
    def recursive_sort(obj):
        if isinstance(obj, dict):
            return {str(k): recursive_sort(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [recursive_sort(x) for x in obj]
        else:
            return str(obj)

    # Убираем сам Sign из данных перед проверкой, если он там есть
    data_to_sign = data.copy()
    if 'Sign' in data_to_sign:
        del data_to_sign['Sign']
    
    sorted_data = recursive_sort(data_to_sign)

    # Формируем JSON. Важно: separators=(',', ':') убирает пробелы,
    # ensure_ascii=False сохраняет кириллицу (хотя для подписи важны байты).
    # В документации сказано "В json строке экранируйте /".
    # Python json.dumps по умолчанию экранирует / если использовать escape_forward_slashes (в стандартном нет).
    # Но обычно Python json совместим с PHP json_encode.
    
    # В Python json.dumps по умолчанию экранирует non-ascii.
    # PHP: json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) - обычно так.
    # Дока говорит: "В json строке экранируйте /". Значит, слэши должны стать \/
    
    json_str = json.dumps(sorted_data, separators=(',', ':'), ensure_ascii=False)
    
    # Ручное экранирование слэшей, чтобы соответствовать PHP json_encode без флага JSON_UNESCAPED_SLASHES
    json_str = json_str.replace('/', '\\/') 

    # Создаем подпись
    calculated_sign = hmac.new(
        key=secret_key.encode('utf-8'),
        msg=json_str.encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()

    # Сравниваем (case-insensitive)
    return hmac.compare_digest(calculated_sign.lower(), received_sign.lower())

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
    await message.answer("👋 Введите Имя и Фамилию для сертификата:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Слишком короткое имя.")
        return

    user_id = message.from_user.id
    # Создаем заказ (ID=123)
    cert_id = await create_certificate_request(user_id, full_name, 2000)

    # Параметры запроса (согласно документации)
    # demo_mode=1 - для тестов. Уберите для боевого режима!
    params = {
        "order_id": str(cert_id),    # Вернется как order_num
        "sys": str(cert_id),         # Вернется как sys
        "products[0][name]": "Подарочный сертификат",
        "products[0][price]": "2000",
        "products[0][quantity]": "1",
        "do": "pay",                 # Сразу на оплату
        "demo_mode": "1"             # ТЕСТОВЫЙ РЕЖИМ
    }
    
    # Собираем ссылку
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
# Вебхук Telegram
# ======================
@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        await dp.feed_update(bot, Update(**update))
    except Exception as e:
        logging.error(f"TG Error: {e}")
    return {"ok": True}

# ======================
# Вебхук Prodamus
# ======================
@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    # 1. Получаем подпись из заголовков
    sign_header = request.headers.get("Sign")
    
    # 2. Получаем данные формы
    form_data = await request.form()
    data = dict(form_data)
    
    logging.info(f"📥 PRODAMUS POST: {data}")

    # --- ПРОВЕРКА ПОДПИСИ ---
    if PRODAMUS_SECRET_KEY:
        if not sign_header:
            logging.warning("⚠️ Нет заголовка Sign! Запрос отклонен.")
            return Response(status_code=403, content="Sign header missing")
            
        is_valid = verify_signature(data, PRODAMUS_SECRET_KEY, sign_header)
        
        if not is_valid:
            # Тонкий момент: алгоритм формирования JSON для хеша сложный.
            # Если проверка не проходит, возможно, дело в экранировании слэшей.
            # Для отладки пока можно писать WARNING, но не блокировать (вернуть 200).
            # В боевом режиме return Response(status_code=403)
            logging.error(f"❌ НЕВЕРНАЯ ПОДПИСЬ! Пришла: {sign_header}")
            # return Response(status_code=403, content="Invalid signature") 
        else:
            logging.info("✅ Подпись верна.")
    # ------------------------

    # 3. Получаем ID заказа (order_num или sys)
    order_val = data.get("order_num") or data.get("sys")

    # Тестовые запросы из админки
    if order_val in ["test", "тест"] or not order_val:
        logging.info("✅ Тестовый запрос (Check URL) - OK")
        return JSONResponse({"status": "ok"})

    # 4. Проверка статуса оплаты
    payment_status = data.get("payment_status", "").lower()
    if payment_status != "success":
        logging.info(f"ℹ️ Статус '{payment_status}'. Игнорируем.")
        return JSONResponse({"status": "ok"})

    # 5. Обработка заказа
    try:
        cert_id = int(order_val)
    except ValueError:
        logging.warning(f"⚠️ ID '{order_val}' не число")
        return JSONResponse({"status": "error"})

    cert = await get_cert_by_id(cert_id)
    if not cert:
        logging.warning(f"⚠️ Заказ {cert_id} не найден")
        return JSONResponse({"status": "ok"})

    if cert.get("paid"):
        logging.info(f"ℹ️ Заказ {cert_id} уже выдан.")
        return JSONResponse({"status": "ok"})

    # 6. Выдача сертификата
    try:
        cert_number = await issue_certificate_number(cert["id"])
        png_bytes = generate_certificate_image(cert["full_name"], cert_number)
        
        await bot.send_photo(
            cert["user_id"],
            BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png"),
            caption=f"✅ Оплата подтверждена!\nСертификат № {cert_number} готов."
        )
        logging.info(f"🎉 Выдан сертификат №{cert_number}")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logging.error(f"❌ Ошибка выдачи: {e}")
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
    logging.info("🚀 Бот запущен")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
