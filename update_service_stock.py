
import sqlite3
from config import Config

# Use the exact database path configured by the application
database_uri = Config.SQLALCHEMY_DATABASE_URI
database_path = database_uri.replace("sqlite:///", "", 1)

connection = sqlite3.connect(database_path)
cursor = connection.cursor()

try:
    # Check existing columns in service_stock
    cursor.execute("PRAGMA table_info(service_stock)")
    stock_columns = [row[1] for row in cursor.fetchall()]

    if "supplier_name" not in stock_columns:
        cursor.execute("""
            ALTER TABLE service_stock
            ADD COLUMN supplier_name VARCHAR(150)
        """)
        print("Added supplier_name")

    if "quantity" not in stock_columns:
        cursor.execute("""
            ALTER TABLE service_stock
            ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1
        """)
        print("Added quantity")

    if "available_quantity" not in stock_columns:
        cursor.execute("""
            ALTER TABLE service_stock
            ADD COLUMN available_quantity INTEGER NOT NULL DEFAULT 1
        """)
        print("Added available_quantity")

    # Check existing columns in service_parts
    cursor.execute("PRAGMA table_info(service_parts)")
    part_columns = [row[1] for row in cursor.fetchall()]

    if "service_stock_id" not in part_columns:
        cursor.execute("""
            ALTER TABLE service_parts
            ADD COLUMN service_stock_id INTEGER
            REFERENCES service_stock(id)
        """)
        print("Added service_stock_id")

    if "unit_cost" not in part_columns:
        cursor.execute("""
        ALTER TABLE service_parts
        ADD COLUMN unit_cost NUMERIC(12, 2) NOT NULL DEFAULT 0
    """)
        print("Added unit_cost")

    connection.commit()
    print("\nDatabase update completed successfully.")

except Exception:
    connection.rollback()
    raise

finally:
    connection.close()
