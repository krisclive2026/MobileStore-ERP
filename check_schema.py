import sqlite3

from config import DATABASE_FILE
from models import db


conn = sqlite3.connect(str(DATABASE_FILE))

actual_tables = {
    row[0]
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )
}

expected_tables = {
    table.name: {
        column.name
        for column in table.columns
    }
    for table in db.metadata.sorted_tables
}

print("\n========== MISSING TABLES ==========")

missing_tables = sorted(
    set(expected_tables) - actual_tables
)

print(missing_tables or "None")


print("\n========== MISSING COLUMNS ==========")

found_missing = False

for table, expected_columns in expected_tables.items():

    if table not in actual_tables:
        continue

    actual_columns = {
        row[1]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        )
    }

    missing_columns = sorted(
        expected_columns - actual_columns
    )

    if missing_columns:
        found_missing = True
        print(f"{table}: {missing_columns}")


if not found_missing:
    print("None")


conn.close()

print("\nSchema inspection completed. No database changes made.")