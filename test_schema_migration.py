
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path("databases/migration_test.db")
BACKUP_PATH = Path(
    "databases/migration_test_before_schema.db"
)

if not DB_PATH.exists():
    raise FileNotFoundError(DB_PATH)

if BACKUP_PATH.exists():
    raise FileExistsError(
        f"Backup already exists: {BACKUP_PATH}"
    )


def get_counts(conn):
    tables = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name NOT LIKE 'sqlite_%'
    """).fetchall()

    return {
        table: conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        for (table,) in tables
    }


# 1. Open database and create a safe SQLite backup.
source = sqlite3.connect(DB_PATH, timeout=30)
backup = sqlite3.connect(BACKUP_PATH)

try:
    source.backup(backup)
    print("Backup created:", BACKUP_PATH)

    integrity = backup.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity != "ok":
        raise RuntimeError(
            f"Backup integrity failed: {integrity}"
        )
finally:
    backup.close()

# 2. Capture original record counts.
source.execute("PRAGMA foreign_keys=ON")
before_counts = get_counts(source)

try:
    source.execute("BEGIN IMMEDIATE")

    # 3. Create missing service_stock table first.
    tables = {
        row[0]
        for row in source.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
        """)
    }

    if "service_stock" not in tables:
        source.execute("""
            CREATE TABLE service_stock (
                id INTEGER NOT NULL,
                purchased_date DATE NOT NULL,
                product_name VARCHAR(150) NOT NULL,
                stock_name VARCHAR(150) NOT NULL,
                contact VARCHAR(120),
                cost_price NUMERIC(12, 2) NOT NULL,
                selling_price NUMERIC(12, 2) NOT NULL,
                supplier_name VARCHAR(150),
                quantity INTEGER NOT NULL,
                available_quantity INTEGER NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id)
            )
        """)
        print("Created table: service_stock")

    # 4. Add missing columns safely.
    migrations = {
        "store_settings": {
            "app_launch_count": "INTEGER NOT NULL DEFAULT 0",
            "app_last_opened": "DATETIME",
        },

        "service_jobs": {
            "technician_contact": "VARCHAR(20)",
            "technician_cost": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
            "other_expenses": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
        },

        "service_parts": {
            "service_stock_id": (
                "INTEGER REFERENCES service_stock(id)"
            ),
            "unit_cost": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
        },

        "imei_records": {
            "purchase_item_id": (
                "INTEGER REFERENCES purchase_items(id)"
            ),
            "purchase_date": "DATE",
            "purchase_price": "FLOAT NOT NULL DEFAULT 0",
            "selling_price": "FLOAT NOT NULL DEFAULT 0",
            "sold_date": "DATETIME",
            "variant": "VARCHAR(100)",
            "color": "VARCHAR(50)",
            "condition": (
                "VARCHAR(30) NOT NULL DEFAULT 'Sealed'"
            ),
            "used_grade": "VARCHAR(30)",
            "source": (
                "VARCHAR(20) NOT NULL DEFAULT 'unknown'"
            ),
        },
    }

    for table, columns in migrations.items():
        existing = {
            row[1]
            for row in source.execute(
                f"PRAGMA table_info({table})"
            )
        }

        for column, definition in columns.items():
            if column not in existing:
                source.execute(
                    f'ALTER TABLE "{table}" '
                    f'ADD COLUMN "{column}" {definition}'
                )
                print(f"Added: {table}.{column}")

    source.commit()
    print("\nMigration transaction committed.")

except Exception:
    source.rollback()
    print("\nMigration failed. Transaction rolled back.")
    raise

finally:
    source.close()

# 5. Verify the migrated database.
check = sqlite3.connect(DB_PATH)
check.execute("PRAGMA foreign_keys=ON")

try:
    integrity = check.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    foreign_key_errors = check.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    after_counts = get_counts(check)

    print("\nINTEGRITY:", integrity)
    print("FOREIGN KEY ERRORS:", foreign_key_errors)

    print("\nRECORD COUNT VERIFICATION:")

    for table, old_count in before_counts.items():
        new_count = after_counts.get(table)

        print(
            f"{table}: {old_count} -> {new_count}"
        )

        if old_count != new_count:
            raise RuntimeError(
                f"Record count changed in {table}"
            )

    if integrity != "ok":
        raise RuntimeError("Integrity check failed.")

    if foreign_key_errors:
        raise RuntimeError(
            "Foreign key check failed."
        )

    print("\nSUCCESS: Schema migration verified.")
    print("Backup retained:", BACKUP_PATH)

finally:
    check.close()
