# database.py
import aiosqlite
import os

# 🔑 Сохраняем базу на Persistent Disk
DB_PATH = "/var/data/users.db"

async def init_db():
    # Создаём директорию, если её нет (для безопасности)
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    # Создаём базу, если файла нет
    if os.path.exists(DB_PATH):
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS certificates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                cert_number TEXT,
                paid BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS counter (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_number INTEGER DEFAULT 0
            )
        ''')
        await db.execute("INSERT OR IGNORE INTO counter (id, last_number) VALUES (1, 0)")
        await db.commit()

async def create_certificate_request(user_id: int, full_name: str, amount: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO certificates (user_id, full_name, amount) VALUES (?, ?, ?)",
            (user_id, full_name, amount)
        )
        cert_id = cursor.lastrowid
        await db.commit()
        return cert_id

async def get_cert_by_id(cert_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM certificates WHERE id = ?", (cert_id,)) as cursor:
            row = await cursor.fetchone()
            if row and not row[5]:  # paid = False
                return {
                    "id": row[0],
                    "user_id": row[1],
                    "full_name": row[2],
                    "amount": row[3],
                    "cert_number": row[4],
                    "paid": bool(row[5])
                }
    return None

async def issue_certificate_number(cert_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем последний номер
        async with db.execute("SELECT last_number FROM counter WHERE id = 1") as cursor:
            result = await cursor.fetchone()
            last_num = result[0] if result else 0
        new_num = last_num + 1
        cert_str = f"{new_num:04d}"

        # Обновляем запись
        await db.execute(
            "UPDATE certificates SET cert_number = ?, paid = TRUE WHERE id = ?",
            (cert_str, cert_id)
        )
        # Обновляем счётчик
        await db.execute("UPDATE counter SET last_number = ? WHERE id = 1", (new_num,))
        await db.commit()
        return cert_str
