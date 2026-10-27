import sqlite3

from config import Config
from sqlalchemy.engine import make_url

database_url = make_url(Config.SQLALCHEMY_DATABASE_URI)

if database_url.drivername != "sqlite":
    raise RuntimeError("This migration is intended for SQLite.")

database_path = database_url.database

if not database_path or database_path == ":memory:":
    raise RuntimeError("Could not identify the SQLite database file.")

connection = sqlite3.connect(database_path)

try:
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(products)")
    }

    if not columns:
        raise RuntimeError(
            "The products table does not exist. Initialize the app first."
        )

    migrations = {
        "product_type": (
            "ALTER TABLE products "
            "ADD COLUMN product_type VARCHAR(20) NOT NULL DEFAULT 'Sealed'"
        ),
        "condition": (
            "ALTER TABLE products ADD COLUMN condition VARCHAR(30)"
        ),
        "warranty_months": (
            "ALTER TABLE products ADD COLUMN warranty_months INTEGER"
        ),
    }

    for column, sql in migrations.items():
        if column not in columns:
            connection.execute(sql)
            print(f"Added column: {column}")
        else:
            print(f"Already exists: {column}")

    connection.commit()
    print("Product database migration completed.")

finally:
    connection.close()