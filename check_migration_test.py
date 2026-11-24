
import sqlite3
from pathlib import Path
from models import db

database = Path("databases/migration_test.db")
conn = sqlite3.connect(database)

print("DATABASE:", database.resolve())

actual_tables = {
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
}

print("\nMISSING TABLES:")

for table in db.metadata.sorted_tables:
    if table.name not in actual_tables:
        print("-", table.name)

print("\nMISSING COLUMNS:")

for table in db.metadata.sorted_tables:
    if table.name not in actual_tables:
        continue

    existing = {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({table.name})"
        )
    }

    missing = {
        column.name
        for column in table.columns
    } - existing

    if missing:
        print(f"\n{table.name}:")
        for column in sorted(missing):
            print("-", column)

conn.close()
print("\nAudit completed.")
