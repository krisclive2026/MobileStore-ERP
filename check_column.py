import sqlite3

DB_PATH = r"C:\PANEER\KriscSoft-projects\Mobileapp\databases\mobile_store.db"

conn = sqlite3.connect(DB_PATH)

columns = conn.execute(
    "PRAGMA table_info(products)"
).fetchall()

print("Current Product columns:")
for column in columns:
    print(column[1])

conn.close()