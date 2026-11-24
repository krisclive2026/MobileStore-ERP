import sqlite3
from config import Config

# Use the exact database path configured by the application
database_uri = Config.SQLALCHEMY_DATABASE_URI
database_path = database_uri.replace("sqlite:///", "", 1)

connection = sqlite3.connect(database_path)
cursor = connection.cursor()

try:
    # Check existing columns in service_parts
    cursor.execute("PRAGMA table_info(service_parts)")
    columns = [column[1] for column in cursor.fetchall()]

    # Add unit_cost if it does not already exist
    if "unit_cost" not in columns:
        cursor.execute("""
            ALTER TABLE service_parts
            ADD COLUMN unit_cost NUMERIC(12, 2) NOT NULL DEFAULT 0
        """)
        print("Added unit_cost column to service_parts.")
    else:
        print("unit_cost column already exists.")

    connection.commit()
    print("Database update completed successfully.")

except sqlite3.Error as error:
    connection.rollback()
    print(f"Database update failed: {error}")

finally:
    connection.close()