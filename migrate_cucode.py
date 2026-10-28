
from app import app
from sqlalchemy import inspect

with app.app_context():
    engine = app.extensions["sqlalchemy"].engine

    with engine.begin() as connection:
        existing = {
            column["name"]
            for column in inspect(connection).get_columns("customers")
        }

        if "customer_code" not in existing:
            connection.exec_driver_sql(
                "ALTER TABLE customers ADD COLUMN customer_code VARCHAR(20)"
            )
            print("Added column: customer_code")
        else:
            print("Column already exists: customer_code")

        # Assign permanent codes to existing customers
        connection.exec_driver_sql("""
            UPDATE customers
            SET customer_code = 'CUST-' || printf('%06d', id)
            WHERE customer_code IS NULL OR customer_code = ''
        """)

        print("Existing customer codes updated.")

    print("Customer database migration completed.")
