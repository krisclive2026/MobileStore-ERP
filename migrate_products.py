
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime

DB_PATH = Path(
    r"C:\PANEER\KriscSoft-projects\Mobileapp\databases\mobile_store.db"
)

def migrate_products():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}\n"
            "Check DB_PATH in migrate_products.py."
        )

    # Back up the database before changing anything.
    backup_path = DB_PATH.with_name(
        f"mobile_store_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    )
    shutil.copy2(DB_PATH, backup_path)
    print(f"Database backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute("PRAGMA busy_timeout = 5000")

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(products)"
            ).fetchall()
        }

        if not columns:
            raise RuntimeError("The products table was not found.")

        # New fields for the updated Product model.
        new_columns = {
            "barcode": "VARCHAR(100)",
            "variant": "VARCHAR(100)",
            "condition": "VARCHAR(30) NOT NULL DEFAULT 'Sealed'",
            "used_grade": "VARCHAR(30)",
            "warranty_months": "INTEGER DEFAULT 0",
            "hsn_code": "VARCHAR(30)",
            "gst_percent": "FLOAT NOT NULL DEFAULT 0",
            "reorder_level": "INTEGER NOT NULL DEFAULT 5",
            "track_imei": "INTEGER NOT NULL DEFAULT 1",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "DATETIME",
        }

        for column_name, column_type in new_columns.items():
            if column_name not in columns:
                conn.execute(
                    f'ALTER TABLE products ADD COLUMN '
                    f'"{column_name}" {column_type}'
                )
                print(f"Added column: {column_name}")

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(products)"
            ).fetchall()
        }

        # Combine old RAM and storage values into variant.
        if {"ram", "storage", "variant"}.issubset(columns):
            conn.execute("""
                UPDATE products
                SET variant =
                    CASE
                        WHEN COALESCE(TRIM(ram), '') != ''
                             AND COALESCE(TRIM(storage), '') != ''
                            THEN TRIM(ram) || ' / ' || TRIM(storage)
                        WHEN COALESCE(TRIM(ram), '') != ''
                            THEN TRIM(ram)
                        WHEN COALESCE(TRIM(storage), '') != ''
                            THEN TRIM(storage)
                        ELSE variant
                    END
                WHERE variant IS NULL OR TRIM(variant) = ''
            """)
            print("Migrated RAM/storage values into variant.")

        # Preserve existing low-stock thresholds.
        if {"low_stock_limit", "reorder_level"}.issubset(columns):
            conn.execute("""
                UPDATE products
                SET reorder_level = low_stock_limit
                WHERE low_stock_limit IS NOT NULL
            """)
            print("Preserved existing low-stock limits.")

        # Initialize updated_at for existing products.
        conn.execute("""
            UPDATE products
            SET updated_at = COALESCE(updated_at, created_at)
        """)

        # A unique index allows existing NULL barcodes,
        # while preventing duplicate non-NULL barcodes.
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            ux_products_barcode ON products(barcode)
        """)

        conn.commit()
        print("Product database migration completed successfully.")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    migrate_products()
