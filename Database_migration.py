
import sqlite3
import shutil
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine
from models import db


# --------------------------------------------------
# SAFETY: ONLY ALLOW THE TWO TEST DATABASES
# --------------------------------------------------

ALLOWED_DATABASES = {
    "legacy_migration_test.db",
    "migration_test.db",
    "populated_migration_test.db",
}

if len(sys.argv) != 2:
    raise SystemExit(
        "Usage: python database_migration.py "
        "populated_migration_test.db"
    )

DB_PATH = Path("databases") / sys.argv[1]

if DB_PATH.name not in ALLOWED_DATABASES:
    raise SystemExit("Blocked: this is not an approved test database.")

if not DB_PATH.exists():
    raise SystemExit(f"Database not found: {DB_PATH}")


# --------------------------------------------------
# MISSING COLUMNS
# Nullable columns are intentional where old records
# cannot safely be assigned an invented value.
# --------------------------------------------------

MIGRATIONS = {
    "customers": {
        "customer_code": "VARCHAR(20)",
        "gst_number": "VARCHAR(30)",
    },

    "suppliers": {
        "company_name": "VARCHAR(150)",
        "contact_person": "VARCHAR(100)",
        "mobile_number_2": "VARCHAR(20)",
        "gst_number": "VARCHAR(30)",
        "supplier_category": "VARCHAR(80)",
        "payment_terms": "VARCHAR(50)",
        "delivery_lead_time": "INTEGER DEFAULT 0",
        "notes": "TEXT",
    },

    "purchases": {
        "user_id": "INTEGER REFERENCES users(id)",
        "purchase_date": "DATETIME",
        "expected_delivery_date": "DATE",
        "actual_delivery_date": "DATE",
        "payment_due_date": "DATE",
    },

    "purchase_items": {
        "subtotal": "FLOAT",
    },

    "sales": {
        "sale_date": "DATETIME",
        "gst_mode": "VARCHAR(20) NOT NULL DEFAULT 'exclusive'",
    },

    "sale_items": {
        "tax_amount": "FLOAT NOT NULL DEFAULT 0",
        "subtotal": "FLOAT",
    },

    "payments": {
        "status": "VARCHAR(30) NOT NULL DEFAULT 'completed'",
    },

    "stock_movements": {
        "updated_at": "DATETIME",
    },

    "expenses": {
        "title": "VARCHAR(150)",
        "notes": "TEXT",
    },

    "audit_logs": {
        "entity": "VARCHAR(100)",
        "ip_address": "VARCHAR(50)",
    },


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
        "service_stock_id": "INTEGER REFERENCES service_stock(id)",
        "unit_cost": "NUMERIC(12, 2) NOT NULL DEFAULT 0",
    },

    "imei_records": {
        "purchase_item_id": "INTEGER REFERENCES purchase_items(id)",
        "source": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
        "purchase_date": "DATE",
        "purchase_price": "FLOAT NOT NULL DEFAULT 0",
        "selling_price": "FLOAT NOT NULL DEFAULT 0",
        "variant": "VARCHAR(100)",
        "color": "VARCHAR(50)",
        "condition": "VARCHAR(30) NOT NULL DEFAULT 'Sealed'",
        "used_grade": "VARCHAR(30)",
        "sale_id": "INTEGER REFERENCES sales(id)",
        "sold_date": "DATETIME",
    },
}


def get_tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def get_columns(conn, table):
    return {
        row[1]
        for row in conn.execute(
            f'PRAGMA table_info("{table}")'
        )
    }


def copy_old_value(conn, table, new_column, old_column):
    tables = get_tables(conn)

    if table not in tables:
        return

    columns = get_columns(conn, table)

    if new_column not in columns or old_column not in columns:
        return

    conn.execute(
        f'UPDATE "{table}" '
        f'SET "{new_column}" = "{old_column}" '
        f'WHERE "{new_column}" IS NULL '
        f'AND "{old_column}" IS NOT NULL'
    )


def apply_migration():
    print("\nDATABASE:", DB_PATH.resolve())

    # Check original database integrity.
    with sqlite3.connect(DB_PATH) as check_conn:
        integrity = check_conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"Original database integrity failed: {integrity}"
            )

        original_fk_errors = list(
            check_conn.execute("PRAGMA foreign_key_check")
        )

    # Make a backup before changing anything.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(
        f"{DB_PATH.stem}_before_migration_{timestamp}.db"
    )

    shutil.copy2(DB_PATH, backup_path)
    print("Backup created:", backup_path.name)

    # Create missing model tables.
    # This does NOT alter existing tables or columns.
    engine = create_engine(
        "sqlite:///" + DB_PATH.resolve().as_posix(),
        connect_args={"timeout": 30},
    )

    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()

    # Add missing columns in one SQLite transaction.
    conn = sqlite3.connect(DB_PATH, timeout=30)

    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")

        tables = get_tables(conn)

        for table, definitions in MIGRATIONS.items():
            if table not in tables:
                print("Table not found; skipped:", table)
                continue

            existing = get_columns(conn, table)

            for column, definition in definitions.items():
                if column in existing:
                    continue

                conn.execute(
                    f'ALTER TABLE "{table}" '
                    f'ADD COLUMN "{column}" {definition}'
                )

                print(f"Added: {table}.{column}")

        # Copy only values with clear equivalent old fields.
        # Never assign historical purchases to an invented user.
        copy_old_value(
            conn, "customers", "gst_number", "gstin"
        )
        copy_old_value(
            conn, "suppliers", "gst_number", "gstin"
        )
        copy_old_value(
            conn, "purchases", "purchase_date", "invoice_date"
        )
        copy_old_value(
            conn, "sales", "sale_date", "invoice_date"
        )
        copy_old_value(
            conn, "stock_movements", "updated_at", "created_at"
        )
        copy_old_value(
            conn, "audit_logs", "entity", "entity_type"
        )

        # Old IMEI purchase_cost is copied only when the new
        # purchase_price is still zero.
        imei_columns = get_columns(conn, "imei_records")

        if {"purchase_price", "purchase_cost"}.issubset(imei_columns):
            conn.execute("""
                UPDATE imei_records
                SET purchase_price = purchase_cost
                WHERE purchase_price = 0
                  AND purchase_cost IS NOT NULL
            """)

        conn.execute("""
            UPDATE imei_records
            SET source = 'supplier'
            WHERE purchase_item_id IS NOT NULL
              AND source = 'unknown'
        """)

        conn.commit()
        print("\nMigration transaction committed.")

    except Exception:
        conn.rollback()
        print("\nMigration failed. Column changes rolled back.")
        raise

    finally:
        conn.close()

    # Verify database integrity and foreign keys.
    with sqlite3.connect(DB_PATH) as verify_conn:
        integrity = verify_conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        final_fk_errors = list(
            verify_conn.execute("PRAGMA foreign_key_check")
        )

        final_tables = get_tables(verify_conn)

    print("\nINTEGRITY CHECK:", integrity)
    print("Original foreign-key errors:", len(original_fk_errors))
    print("Final foreign-key errors:", len(final_fk_errors))

    if final_fk_errors:
        print(
            "Foreign-key violations exist. "
            "Compare with the original database; "
            "do not delete records automatically."
        )

    missing_tables = sorted(
        set(db.metadata.tables) - final_tables
    )

    if missing_tables:
        raise RuntimeError(
            f"Tables still missing: {missing_tables}"
        )

    if integrity != "ok":
        raise RuntimeError("Database integrity verification failed.")

    print("\nMIGRATION COMPLETED SUCCESSFULLY.")
    print("Existing tables and records were not intentionally deleted.")


if __name__ == "__main__":
    apply_migration()
