
from sqlalchemy import inspect, text
from app import app, db

NEW_COLUMNS = {
    "company_name": "VARCHAR(150)",
    "contact_person": "VARCHAR(100)",
    "mobile_number_2": "VARCHAR(20)",
    "supplier_category": "VARCHAR(80)",
    "payment_terms": "VARCHAR(50)",
    "delivery_lead_time": "INTEGER DEFAULT 0",
    "notes": "TEXT",
    "is_active": "BOOLEAN NOT NULL DEFAULT 1",
}

with app.app_context():
    inspector = inspect(db.engine)

    if "suppliers" not in inspector.get_table_names():
        raise RuntimeError(
            "Suppliers table not found. Check the database configuration."
        )

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("suppliers")
    }

    with db.engine.begin() as connection:
        for column_name, column_type in NEW_COLUMNS.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        f'ALTER TABLE suppliers ADD COLUMN '
                        f'"{column_name}" {column_type}'
                    )
                )
                print(f"Added column: {column_name}")
            else:
                print(f"Already exists: {column_name}")

    print("Supplier migration completed successfully.")
