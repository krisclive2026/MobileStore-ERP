import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

DB_PATH = Path("databases/mobile_store_new.db")

if not DB_PATH.exists():
    raise FileNotFoundError(f"Database not found: {DB_PATH}")

# Backup before migration
backup_path = DB_PATH.with_name(
    f"mobile_store_new_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
)
shutil.copy2(DB_PATH, backup_path)

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

def add_column_if_missing(table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = [row[1] for row in cursor.fetchall()]

    if column not in existing:
        cursor.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
        print(f"Added: {table}.{column}")
    else:
        print(f"Already exists: {table}.{column}")

try:
    add_column_if_missing(
        "service_parts", "unit_cost",
        "NUMERIC(12, 2) NOT NULL DEFAULT 0"
    )

    add_column_if_missing(
        "service_jobs", "technician_cost",
        "NUMERIC(12, 2) NOT NULL DEFAULT 0"
    )

    add_column_if_missing(
        "service_jobs", "other_expenses",
        "NUMERIC(12, 2) NOT NULL DEFAULT 0"
    )

    conn.commit()
    print("\nMigration completed successfully.")

except Exception:
    conn.rollback()
    raise

finally:
    conn.close()

print(f"Backup saved at: {backup_path}")