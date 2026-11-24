
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

from config import DATA_DIR
from models import db

from utils.database_backup import create_database_backup

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
        "supplier_id": "INTEGER REFERENCES suppliers(id)",
        "source": "VARCHAR(20) NOT NULL DEFAULT 'unknown'",
        "purchase_date": "DATE",
        "purchase_price": "FLOAT NOT NULL DEFAULT 0",
        "selling_price": "FLOAT NOT NULL DEFAULT 0",
        "variant": "VARCHAR(100)",
        "color": "VARCHAR(50)",
        "condition": "VARCHAR(30) NOT NULL DEFAULT 'Sealed'",
        "used_grade": "VARCHAR(30)",
        "warranty_months": "INTEGER NOT NULL DEFAULT 0",
        "sale_warranty_months": "INTEGER NOT NULL DEFAULT 0",
        "gst_percent": "FLOAT NOT NULL DEFAULT 0",
        "gst_percent": "FLOAT NOT NULL DEFAULT 0",
        "is_active": "BOOLEAN NOT NULL DEFAULT 1",
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


def get_foreign_key_errors(conn):
    return {
        tuple(row)
        for row in conn.execute("PRAGMA foreign_key_check")
    }


def copy_old_value(conn, table, new_column, old_column):
    if table not in get_tables(conn):
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


def run_schema_migration(app):
    with app.app_context():

        db_uri = app.config["SQLALCHEMY_DATABASE_URI"]

        if not db_uri.startswith("sqlite:///"):
            raise RuntimeError(
                "Schema migration supports SQLite databases only."
            )

        db_path = Path(
            db_uri.replace("sqlite:///", "", 1)
        )

        db_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        print("[SCHEMA MIGRATION] Database:", db_path)

        database_existed = db_path.exists()
        original_fk_errors = set()

        # -------------------------------------------------
        # 1. Check and back up existing database
        # -------------------------------------------------

        if database_existed:

            with sqlite3.connect(
                db_path,
                timeout=30
            ) as check_conn:

                integrity = check_conn.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

                if integrity != "ok":
                    raise RuntimeError(
                        f"Database integrity check failed: {integrity}"
                    )

                original_fk_errors = get_foreign_key_errors(
                    check_conn
                )

            backup_path = create_database_backup(
                category="before_migration"
            )

            print(
                "[SCHEMA MIGRATION] Backup:",
                backup_path.name
            )

        else:
            print(
                "[SCHEMA MIGRATION] Fresh database. "
                "No backup needed."
            )

        # -------------------------------------------------
        # 2. Create missing tables
        # -------------------------------------------------

        db.create_all()

        # -------------------------------------------------
        # 3. Add missing columns
        # -------------------------------------------------

        conn = sqlite3.connect(
            db_path,
            timeout=30
        )

        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")

            tables = get_tables(conn)

            for table, definitions in MIGRATIONS.items():

                if table not in tables:
                    continue

                existing = get_columns(
                    conn,
                    table
                )

                for column, definition in definitions.items():

                    if column in existing:
                        continue

                    conn.execute(
                        f'ALTER TABLE "{table}" '
                        f'ADD COLUMN "{column}" {definition}'
                    )

                    print(
                        f"[SCHEMA MIGRATION] Added "
                        f"{table}.{column}"
                    )

                    existing.add(column)

            # -------------------------------------------------
            # 4. Copy values from older column names
            # -------------------------------------------------

            copy_old_value(
                conn,
                "customers",
                "gst_number",
                "gstin"
            )

            copy_old_value(
                conn,
                "suppliers",
                "gst_number",
                "gstin"
            )

            copy_old_value(
                conn,
                "purchases",
                "purchase_date",
                "invoice_date"
            )

            copy_old_value(
                conn,
                "sales",
                "sale_date",
                "invoice_date"
            )

            copy_old_value(
                conn,
                "stock_movements",
                "updated_at",
                "created_at"
            )

            copy_old_value(
                conn,
                "audit_logs",
                "entity",
                "entity_type"
            )

            # -------------------------------------------------
            # 5. Preserve older IMEI purchase values
            # -------------------------------------------------

            imei_columns = get_columns(
                conn,
                "imei_records"
            )

            if {
                "purchase_price",
                "purchase_cost"
            }.issubset(imei_columns):

                conn.execute("""
                    UPDATE imei_records
                    SET purchase_price = purchase_cost
                    WHERE purchase_price = 0
                      AND purchase_cost IS NOT NULL
                """)

            if "imei_records" in get_tables(conn):

                conn.execute("""
                    UPDATE imei_records
                    SET source = 'supplier'
                    WHERE purchase_item_id IS NOT NULL
                      AND source = 'unknown'
                """)

            conn.commit()

            print(
                "[SCHEMA MIGRATION] Transaction committed."
            )

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        # -------------------------------------------------
        # 6. Verify database after migration
        # -------------------------------------------------

        with sqlite3.connect(
            db_path,
            timeout=30
        ) as verify_conn:

            integrity = verify_conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            final_tables = get_tables(
                verify_conn
            )

            final_fk_errors = get_foreign_key_errors(
                verify_conn
            )

            final_imei_columns = get_columns(
                verify_conn,
                "imei_records"
            )

            if "warranty_months" not in final_imei_columns:
                raise RuntimeError(
                "Migration failed: imei_records.warranty_months is missing."
                )

            if "sale_warranty_months" not in final_imei_columns:
                raise RuntimeError(
                "Migration failed: imei_records.sale_warranty_months is missing."
                )

            if "is_active" not in final_imei_columns:
                raise RuntimeError(
                 "Migration failed: imei_records.is_active is missing."
                )

            if "gst_percent" not in final_imei_columns:
                raise RuntimeError(
                    "Migration failed: imei_records.gst_percent is missing."
                )

        missing_tables = sorted(
            set(db.metadata.tables) - final_tables
        )

        if integrity != "ok":
            raise RuntimeError(
                f"Post-migration integrity check failed: {integrity}"
            )

        if missing_tables:
            raise RuntimeError(
                f"Tables missing after migration: {missing_tables}"
            )

        # -------------------------------------------------
        # 7. Compare foreign-key violations
        # -------------------------------------------------

        new_fk_errors = (
            final_fk_errors - original_fk_errors
        )

        resolved_fk_errors = (
            original_fk_errors - final_fk_errors
        )

        print(
            "[SCHEMA MIGRATION] Original foreign-key errors:",
            len(original_fk_errors)
        )

        print(
            "[SCHEMA MIGRATION] Final foreign-key errors:",
            len(final_fk_errors)
        )

        if resolved_fk_errors:
            print(
                "[SCHEMA MIGRATION] Resolved foreign-key errors:",
                len(resolved_fk_errors)
            )

        if new_fk_errors:
            raise RuntimeError(
                "Migration introduced new foreign-key violations: "
                f"{len(new_fk_errors)}"
            )

        print(
            "[SCHEMA MIGRATION] Integrity check: OK"
        )

        print(
            "[SCHEMA MIGRATION] Completed successfully."
        )
