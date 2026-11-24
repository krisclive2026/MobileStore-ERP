
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime


# =====================================================
# DATABASE CONFIGURATION
# =====================================================

import os

DB_PATH = (
    Path(os.environ["LOCALAPPDATA"])
    / "MobileStoreERP"
    / "databases"
    / "mobile_store_new.db"
)

# =====================================================
# DATABASE MIGRATION
# =====================================================

def run_migrations():

    print("\n========== IMEI DATABASE MIGRATION ==========\n")

    # Check database
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    # Create backup
    BACKUP_PATH = DB_PATH.with_name(
        f"mobile_store_backup_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    )

    # Connect to source database
    source = sqlite3.connect(DB_PATH)

    try:

        # Create a consistent backup
        backup = sqlite3.connect(BACKUP_PATH)

        try:
            source.backup(backup)
        finally:
            backup.close()

    finally:
        source.close()

    print(f"[BACKUP] Created: {BACKUP_PATH}")

    # =================================================
    # CONNECT TO DATABASE
    # =================================================

    conn = sqlite3.connect(DB_PATH)

    try:

        cursor = conn.cursor()

        # Enable foreign key enforcement
        cursor.execute("PRAGMA foreign_keys = ON")

        # Check required tables
        cursor.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
        """)

        tables = {
            row[0] for row in cursor.fetchall()
        }

        if "imei_records" not in tables:
            raise RuntimeError(
                "imei_records table does not exist."
            )

        if "purchase_items" not in tables:
            raise RuntimeError(
                "purchase_items table does not exist."
            )

        # =============================================
        # CHECK EXISTING COLUMNS
        # =============================================

        cursor.execute(
            "PRAGMA table_info(imei_records)"
        )

        existing_columns = {
            row[1] for row in cursor.fetchall()
        }

        # =============================================
        # NEW COLUMNS
        # =============================================

        new_columns = {

            "purchase_item_id":
                "INTEGER REFERENCES purchase_items(id)",

            "purchase_date":
                "DATE",

            "purchase_price":
                "FLOAT NOT NULL DEFAULT 0",

            "selling_price":
                "FLOAT NOT NULL DEFAULT 0",

            "sold_date":
                "DATETIME",

            # Individual handset specifications
            "variant":
                "VARCHAR(100)",

            "color":
                "VARCHAR(50)",

            "condition":
                "VARCHAR(30) NOT NULL DEFAULT 'Sealed'",

            "used_grade":
                "VARCHAR(30)",

            # Stock source tracking
            "source":
                "VARCHAR(20) NOT NULL DEFAULT 'unknown'"
        }

        # =============================================
        # START TRANSACTION
        # =============================================

        cursor.execute("BEGIN")

        for column_name, column_definition in new_columns.items():

            if column_name not in existing_columns:

                cursor.execute(
                    f"""
                    ALTER TABLE imei_records
                    ADD COLUMN {column_name} {column_definition}
                    """
                )

                print(f"[ADDED] {column_name}")

            else:

                print(f"[EXISTS] {column_name}")

        # =============================================
        # CLASSIFY EXISTING RECORDS
        # =============================================

        cursor.execute("""
            UPDATE imei_records
            SET source = 'supplier'
            WHERE purchase_item_id IS NOT NULL
              AND source = 'unknown'
        """)

        print(
            f"[UPDATED] {cursor.rowcount} records "
            "classified as supplier"
        )

        # =============================================
        # CREATE INDEXES
        # =============================================

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            ix_imei_records_purchase_item_id
            ON imei_records (purchase_item_id)
        """)

        print("[INDEX] purchase_item_id index verified")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
            ix_imei_records_source
            ON imei_records (source)
        """)

        print("[INDEX] source index verified")

        # =============================================
        # COMMIT
        # =============================================

        conn.commit()

        print("\n[SUCCESS] Migration completed successfully!")

    except Exception as error:

        conn.rollback()

        print("\n[ERROR] Migration failed!")
        print(error)

        raise

    finally:

        conn.close()

    print(f"\n[BACKUP LOCATION] {BACKUP_PATH}")
    print("\n==============================================\n")


# =====================================================
# EXECUTION
# =====================================================

if __name__ == "__main__":
    run_migrations()
