import sqlite3
from pathlib import Path

# Database path
DATABASE_FILE = (
    Path(__file__).resolve().parent
    / "databases"
    / "mobile_store_new.db"
)

def update_database():
    connection = sqlite3.connect(DATABASE_FILE)
    cursor = connection.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(store_settings)")
    columns = [
        column[1]
        for column in cursor.fetchall()
    ]

    # Add application launch count
    if "app_launch_count" not in columns:
        cursor.execute("""
            ALTER TABLE store_settings
            ADD COLUMN app_launch_count INTEGER NOT NULL DEFAULT 0
        """)

    # Add last application launch time
    if "app_last_opened" not in columns:
        cursor.execute("""
            ALTER TABLE store_settings
            ADD COLUMN app_last_opened DATETIME
        """)

    connection.commit()
    connection.close()

    print("Database updated successfully.")


if __name__ == "__main__":
    update_database()