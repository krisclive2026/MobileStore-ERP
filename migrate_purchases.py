from app import app
from sqlalchemy import inspect

with app.app_context():
    engine = app.extensions["sqlalchemy"].engine

    required_columns = {
        "expected_delivery_date": "DATETIME",
        "actual_delivery_date": "DATETIME",
        "payment_due_date": "DATETIME",
    }

    with engine.begin() as connection:
        existing = {
            column["name"]
            for column in inspect(connection).get_columns("purchases")
        }

        for name, column_type in required_columns.items():
            if name not in existing:
                connection.exec_driver_sql(
                    f"ALTER TABLE purchases ADD COLUMN {name} {column_type}"
                )
                print(f"Added column: {name}")
            else:
                print(f"Already exists: {name}")

    print("Purchase database migration completed.")