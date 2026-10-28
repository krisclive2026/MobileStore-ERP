
import sqlite3
from pathlib import Path

from app import app, db

# Columns missing from the existing SQLite database.
# Existing columns and existing records will be preserved.
NEW_COLUMNS = {
    "opening_time": "VARCHAR(20) DEFAULT '09:00'",
    "closing_time": "VARCHAR(20) DEFAULT '21:00'",
    "invoice_prefix": "VARCHAR(30) DEFAULT 'INV-'",
    "show_customer_id": "BOOLEAN NOT NULL DEFAULT 1",
    "show_gstin": "BOOLEAN NOT NULL DEFAULT 1",
    "show_tax_breakdown": "BOOLEAN NOT NULL DEFAULT 1",
    "show_emi_details": "BOOLEAN NOT NULL DEFAULT 1",
    "margin_top_mm": "FLOAT DEFAULT 10",
    "margin_bottom_mm": "FLOAT DEFAULT 10",
    "font_size": "FLOAT DEFAULT 12",
    "instagram_url": "VARCHAR(255)",
    "facebook_url": "VARCHAR(255)",
    "whatsapp_url": "VARCHAR(255)",
}


def migrate_store_settings():
    with app.app_context():
        engine = db.engine

        inspector = db.inspect(engine)
        if "store_settings" not in inspector.get_table_names():
            print("ERROR: store_settings table does not exist.")
            print("Check your database configuration.")
            return

        existing_columns = {
            column["name"]
            for column in inspector.get_columns("store_settings")
        }

        added = []

        try:
            with engine.begin() as connection:
                for column_name, column_definition in NEW_COLUMNS.items():
                    if column_name not in existing_columns:
                        sql = (
                            f'ALTER TABLE store_settings '
                            f'ADD COLUMN "{column_name}" '
                            f'{column_definition}'
                        )
                        connection.exec_driver_sql(sql)
                        added.append(column_name)
                        print(f"Added column: {column_name}")

            print("\nStore settings migration completed.")
            if not added:
                print("All required columns already exist.")

        except Exception:
            print("\nMigration failed. Check the error above.")
            raise


if __name__ == "__main__":
    migrate_store_settings()
