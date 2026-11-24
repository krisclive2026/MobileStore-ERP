import sqlite3
from config import DATABASE_FILE

conn = sqlite3.connect(str(DATABASE_FILE))
conn.row_factory = sqlite3.Row

tables = {
    "audit_logs": [2, 4, 5, 6, 7],
    "sales": [1],
    "stock_movements": [1],
}

for table, rowids in tables.items():

    print(f"\n========== {table} ==========")

    foreign_keys = conn.execute(
        f'PRAGMA foreign_key_list("{table}")'
    ).fetchall()

    for fk in foreign_keys:
        print("FOREIGN KEY:", dict(fk))

    placeholders = ",".join("?" for _ in rowids)

    rows = conn.execute(
        f'SELECT rowid, * FROM "{table}" '
        f'WHERE rowid IN ({placeholders})',
        rowids
    ).fetchall()

    print("AFFECTED ROWS:")

    for row in rows:
        print(dict(row))

conn.close()

print("\nInspection completed. No database changes made.")