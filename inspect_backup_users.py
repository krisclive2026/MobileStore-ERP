import sqlite3
from pathlib import Path

folder = Path("databases")

for db_file in sorted(folder.glob("*.db")):

    try:
        conn = sqlite3.connect(str(db_file))

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        if "users" in tables:
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(users)")
            }

            if {"id", "username", "role"}.issubset(columns):
                users = conn.execute(
                    "SELECT id, username, role "
                    "FROM users WHERE id IN (1, 2)"
                ).fetchall()

                if users:
                    print(f"\nDATABASE: {db_file.name}")
                    for user in users:
                        print(user)

        conn.close()

    except sqlite3.Error as error:
        print(f"Could not inspect {db_file.name}: {error}")

print("\nBackup inspection completed. No database changes made.")