import os
import logging
import asyncio
import aiosqlite
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

# Импорты ваших вспомогательных модулей
# Убедитесь, что файлы database.py и certificate_generator.py находятся в той же папке
from database import init_db, create_certificate_request, get_cert_by_id, issue_certificate_number
from certificate_generator import generate_certificate_image

# ======================
# Настройки
# ======================
# Токен телеграм бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required! Укажите его в .env или переменных окружения")

# Секретный ключ Продамуса (для проверки подписи в будущем, если понадобится)
PRODAMUS_SECRET_KEY = os.getenv("PRODAMUS_SECRET_KEY", "")

# Пути для вебхуков
TELEGRAM_WEBHOOK_PATH = "/webhook"
PRODAMUS_WEBHOOK_PATH = "/prodamus-webhook"

# Определение адреса хоста (автоматически для Render или localhost)
render_host = os.getenv("RENDER_EXTERNAL_HOSTNAME")
BASE_URL = f"https://{render_host}" if render_host else "http://localhost:8000"

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
# Telegram Handlers (Логика бота)
# ======================

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Начало диалога"""
    await message.answer("👋 Привет! Введите имя и фамилию для сертификата:")
    await state.set_state(UserStates.waiting_for_name)

@router.message(UserStates.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    """Обработка имени и генерация ссылки на оплату"""
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Слишком короткое имя. Попробуйте снова:")
        return

    user_id = message.from_user.id
    
    # 1. Создаем заявку в БД
    cert_id = await create_certificate_request(user_id, full_name, 2000)

    # 2. Формируем ссылку для Продамуса
    # ВАЖНО: 
    # order_id={cert_id} -> вернется в вебхуке как order_num (основной ID)
    # sys={cert_id}      -> вернется в вебхуке как sys (резервный способ)
    # demo_mode=1        -> тестовый режим (уберите для боевых платежей!)
    pay_link = f"https://payform.ru/jga8Qsz/?order_id={cert_id}&sys={cert_id}&demo_mode=1"

    await message.answer(
        f"Заказ №{cert_id} создан для {full_name}.\n"
        "Для получения сертификата оплатите заказ:",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="💳 Оплатить 2000 ₽", url=pay_link)]
            ]
        )
    )
    await state.clear()

# ======================
# Тестовые команды (Админка)
# ======================
@router.message(Command("listusers"))
async def list_users(message: Message):
    # Укажите здесь свой Telegram ID для безопасности
    if message.from_user.id != 8568411350: 
        await message.answer("❌ Нет доступа")
        return

    async with aiosqlite.connect("users.db") as db:
        try:
            async with db.execute("SELECT id, full_name, paid FROM certificates ORDER BY id DESC LIMIT 5") as cursor:
                rows = await cursor.fetchall()
        except Exception:
            await message.answer("База данных пуста или ошибка.")
            return

    text = "📋 Последние заявки:\n"
    for row in rows:
        cid, name, paid = row
        status = "✅" if paid else "❌"
        text += f"ID: {cid} | {status} | {name}\n"
    await message.answer(text)

# ======================
# WEBHOOKS (Самое важное)
# ======================

# 1. Вебхук Telegram
@app.post(TELEGRAM_WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        update = await request.json()
        await dp.feed_update(bot, Update(**update))
    except Exception as e:
        logging.error(f"Telegram webhook error: {e}")
    return {"ok": True}

# 2. Вебхук Prodamus
@app.post(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook(request: Request):
    """Обработчик уведомлений об оплате"""
    
    # Считываем данные формы
    form_data = await request.form()
    data = dict(form_data)
    
    # Логируем входящие данные (для отладки)
    logging.info(f"📥 PRODAMUS DATA: {data}")

    # --- Шаг 1: Проверка статуса оплаты ---
    # Нас интересует только success.
    # Если статус order_canceled, order_denied и т.д. - игнорируем.
    payment_status = data.get("payment_status", "").lower()
    if payment_status != "success":
        logging.info(f"ℹ️ Статус оплаты '{payment_status}'. Пропускаем.")
        return JSONResponse({"status": "ok", "message": "Ignored non-success status"})

    # --- Шаг 2: Поиск ID заказа ---
    # Продамус может прислать ID в order_num или sys
    order_val = data.get("order_num") or data.get("sys")

    # Обработка тестовых запросов "Проверить URL" из админки Продамуса
    if order_val in ["test", "тест"] or data.get("test") == "1":
        logging.info("✅ Получен тестовый запрос от Продамуса.")
        return JSONResponse({"status": "ok"})

    if not order_val:
        logging.warning("⚠️ Не удалось найти ID заказа в запросе")
        return JSONResponse({"status": "error", "message": "No ID found"})

    # --- Шаг 3: Обработка заказа ---
    try:
        cert_id = int(order_val)
    except ValueError:
        logging.warning(f"⚠️ ID '{order_val}' не является числом")
        return JSONResponse({"status": "error", "message": "Invalid ID format"})

    # Ищем в базе
    cert = await get_cert_by_id(cert_id)
    if not cert:
        logging.warning(f"⚠️ Сертификат с ID {cert_id} не найден в БД")
        # Возвращаем OK, чтобы Продамус не пытался слать этот запрос вечно
        return JSONResponse({"status": "ok", "message": "Certificate not found"})

    # Если уже оплачен, не высылаем повторно
    if cert.get("paid"):
        logging.info(f"ℹ️ Сертификат {cert_id} уже был выдан ранее.")
        return JSONResponse({"status": "ok"})

    # --- Шаг 4: Выдача сертификата ---
    try:
        # 1. Присваиваем номер и ставим статус "оплачено" в БД
        cert_number = await issue_certificate_number(cert["id"])
        
        # 2. Генерируем картинку
        png_bytes = generate_certificate_image(cert["full_name"], cert_number)
        
        # 3. Отправляем в Telegram пользователю
        await bot.send_photo(
            cert["user_id"],
            BufferedInputFile(png_bytes, filename=f"cert_{cert_number}.png"),
            caption=f"🎉 Поздравляем! Оплата прошла успешно.\nВаш сертификат № {cert_number} готов."
        )
        
        logging.info(f"✅ УСПЕХ: Сертификат №{cert_number} выдан пользователю {cert['user_id']}")
        return JSONResponse({"status": "ok"})

    except Exception as e:
        logging.error(f"❌ ОШИБКА при выдаче сертификата: {e}")
        # Если ошибка на нашей стороне (например, Telegram лежит), 
        # возвращаем 500, чтобы Продамус попробовал позже
        return Response(status_code=500)

# Заглушка для GET (если открыть ссылку в браузере)
@app.get(PRODAMUS_WEBHOOK_PATH)
async def prodamus_webhook_get():
    return {"status": "ok", "message": "Use POST method"}

# ======================
# Запуск приложения
# ======================
@app.on_event("startup")
async def on_startup():
    await init_db()
    
    # Установка вебхука Telegram
    webhook_url = f"{BASE_URL}{TELEGRAM_WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    
    logging.info("🚀 Сервер запущен")
    logging.info(f"🔗 Telegram Webhook: {webhook_url}")
    logging.info(f"🔗 Prodamus URL: {BASE_URL}{PRODAMUS_WEBHOOK_PATH}")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
