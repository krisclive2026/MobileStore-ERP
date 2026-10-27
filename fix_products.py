
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

# IMPORTANT:
# Set this to the exact SQLite database path used by your app.
DB_PATH = Path(__file__).resolve().parent / "databases" / "mobile_store_new.db"

if not DB_PATH.is_absolute():
    DB_PATH = (Path(__file__).resolve().parent / DB_PATH).resolve()

if not DB_PATH.exists():
    raise FileNotFoundError(
        f"Database not found: {DB_PATH}\n"
        "Update DB_PATH in this script to match config.py."
    )

# Create a backup before changing the database.
backup_path = DB_PATH.with_name(
    f"{DB_PATH.stem}_backup_{datetime.now():%Y%m%d_%H%M%S}{DB_PATH.suffix}"
)
shutil.copy2(DB_PATH, backup_path)
print(f"Backup created: {backup_path}")

column_definitions = {
    "barcode": "VARCHAR(100)",
    "variant": "VARCHAR(100)",
    "condition": "VARCHAR(30) NOT NULL DEFAULT 'Sealed'",
    "used_grade": "VARCHAR(30)",
    "warranty_months": "INTEGER DEFAULT 0",
    "hsn_code": "VARCHAR(30)",
    "gst_percent": "FLOAT NOT NULL DEFAULT 0",
    "reorder_level": "INTEGER NOT NULL DEFAULT 5",
    "track_imei": "BOOLEAN NOT NULL DEFAULT 1",
    "is_active": "BOOLEAN NOT NULL DEFAULT 1",
    "updated_at": "DATETIME",
}

with sqlite3.connect(DB_PATH) as conn:
    conn.execute("PRAGMA busy_timeout = 5000")

    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(products)").fetchall()
    }

    if not existing:
        raise RuntimeError(
            "The products table was not found. "
            "Verify DB_PATH in config.py."
        )

    for column, definition in column_definitions.items():
        if column not in existing:
            conn.execute(
                f'ALTER TABLE products ADD COLUMN "{column}" {definition}'
            )
            print(f"Added column: {column}")
        else:
            print(f"Already exists: {column}")

    conn.commit()

print("Database schema update finished.")
print(f"Database updated: {DB_PATH}")
