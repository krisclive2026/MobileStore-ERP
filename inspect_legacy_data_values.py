
import sqlite3

DB = "databases/mobile_store.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

checks = {
    "customers": ["id", "name", "gstin"],
    "suppliers": ["id", "name", "gstin"],
    "purchases": ["id", "invoice_number", "invoice_date", "supplier_id"],
    "sales": ["id", "invoice_number", "invoice_date", "user_id"],
    "purchase_items": ["id", "purchase_id", "quantity", "unit_cost", "line_total"],
    "sale_items": ["id", "sale_id", "quantity", "unit_price", "line_total"],
    "expenses": ["id", "category", "description"],
    "audit_logs": ["id", "entity_type", "entity_id"],
    "imei_records": [
        "id", "imei1", "purchase_cost", "sale_item_id",
        "status", "warranty_start", "warranty_end"
    ]
}

try:
    for table, columns in checks.items():
        available = {
            row["name"]
            for row in conn.execute(
                f'PRAGMA table_info("{table}")'
            )
        }

        selected = [c for c in columns if c in available]

        print(f"\n--- {table} ---")

        if not selected:
            print("No matching columns")
            continue

        sql = (
            f'SELECT {", ".join(chr(34)+c+chr(34) for c in selected)} '
            f'FROM "{table}" LIMIT 5'
        )

        rows = conn.execute(sql).fetchall()

        if not rows:
            print("No records")
        else:
            for row in rows:
                print(dict(row))

finally:
    conn.close()
