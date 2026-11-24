
import sqlite3
from pathlib import Path

DB_PATH = Path("databases/legacy_migration_test.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print("=" * 60)
print("LEGACY DATABASE INSPECTION")
print("=" * 60)

tables = [
    row["name"]
    for row in conn.execute("""
        SELECT name FROM sqlite_master
        WHERE type = 'table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
]

print("\n--- EXISTING TABLES AND RECORD COUNTS ---")

for table in tables:
    count = conn.execute(
        f'SELECT COUNT(*) FROM "{table}"'
    ).fetchone()[0]
    print(f"{table}: {count} records")

print("\n--- FOREIGN KEY CHECK ---")

errors = conn.execute("PRAGMA foreign_key_check").fetchall()

if errors:
    print("Foreign-key errors:", len(errors))
    for error in errors:
        print(tuple(error))
else:
    print("No foreign-key errors.")

print("\n--- EXISTING SCHEMA DETAILS ---")

inspect_tables = [
    "customers",
    "suppliers",
    "purchases",
    "sales",
    "stock_movements",
    "payments",
    "purchase_items",
    "sale_items",
    "imei_records",
    "audit_logs",
    "expenses",
]

for table in inspect_tables:
    if table not in tables:
        continue

    print(f"\nTABLE: {table}")

    for column in conn.execute(
        f'PRAGMA table_info("{table}")'
    ):
        print(
            f"  {column['name']} | "
            f"{column['type']} | "
            f"NOT NULL={column['notnull']} | "
            f"DEFAULT={column['dflt_value']} | "
            f"PK={column['pk']}"
        )

conn.close()

print("\nInspection completed.")
