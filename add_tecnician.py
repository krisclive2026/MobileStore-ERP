import sqlite3
from config import DATABASE_FILE

connection = sqlite3.connect(str(DATABASE_FILE))

cursor = connection.cursor()

cursor.execute("PRAGMA table_info(service_jobs)")
columns = [column[1] for column in cursor.fetchall()]

if "technician_contact" not in columns:
    cursor.execute("""
        ALTER TABLE service_jobs
        ADD COLUMN technician_contact VARCHAR(20)
    """)
    connection.commit()
    print("Technician contact column added successfully.")
else:
    print("Technician contact column already exists.")

connection.close()