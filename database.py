import aiosqlite
import os

DB_PATH = "users.db"

async def init_db():
    if os.path.exists(DB_PATH):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
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

async def save_user(user_id: int, full_name: str, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, full_name, amount) VALUES (?, ?, ?)",
            (user_id, full_name, amount)
        )
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "user_id": row[0],
                    "full_name": row[1],
                    "amount": row[2],
                    "cert_number": row[3],
                    "paid": bool(row[4])
                }
    return None

async def issue_certificate_number(user_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        # Получаем текущий последний номер
        async with db.execute("SELECT last_number FROM counter WHERE id = 1") as cursor:
            result = await cursor.fetchone()
            last_num = result[0] if result else 0
        new_num = last_num + 1
        cert_str = f"{new_num:04d}"  # 1 → '0001'
        # Обновляем номер у пользователя
        await db.execute(
            "UPDATE users SET cert_number = ?, paid = TRUE WHERE user_id = ?",
            (cert_str, user_id)
        )
        # Сохраняем новый последний номер
        await db.execute("UPDATE counter SET last_number = ? WHERE id = 1", (new_num,))
        await db.commit()
        return cert_str
