
import sqlite3
from pathlib import Path

DB_PATH = Path("databases/mobile_store_new.db")

DELETE_ORDER = [
    "used_handset_sellers",
    "service_history",
    "service_parts",
    "payments",
    "direct_stock_payments",
    "used_seller_payments",
    "imei_records",
    "sale_items",
    "purchase_items",
    "stock_movements",
    "expenses",
    "audit_logs",
    "service_jobs",
    "service_stock",
    "sales",
    "purchases",
    "customers",
    "suppliers",
    "products",
]

conn = sqlite3.connect(DB_PATH)

try:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")

    # Clear business data
    for table in DELETE_ORDER:
        conn.execute(f'DELETE FROM "{table}"')
        print(f"Cleared: {table}")

    # Verify foreign-key integrity
    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if fk_errors:
        raise Exception(
            f"Foreign-key errors found: {fk_errors}"
        )

    # Commit only after successful verification
    conn.commit()

    print("\nDatabase reset completed successfully.")

    print("\nPreserved records:")
    print(
        "Users:",
        conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    )
    print(
        "Store settings:",
        conn.execute("SELECT COUNT(*) FROM store_settings").fetchone()[0]
    )

    print("\nRemaining business records:")
    for table in DELETE_ORDER:
        count = conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        print(f"{table}: {count}")

except Exception as error:
    conn.rollback()
    print("\nReset failed. Changes rolled back.")
    print(error)

finally:
    conn.close()
