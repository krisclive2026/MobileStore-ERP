
import sqlite3
from pathlib import Path
from models import db

expected = {
    table.name: {column.name for column in table.columns}
    for table in db.metadata.sorted_tables
}

for file in sorted(Path("databases").glob("*.db")):
    conn = sqlite3.connect(file)

    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    print("\n" + "=" * 55)
    print("DATABASE:", file.name)

    for table, columns in expected.items():
        if table not in actual:
            print("MISSING TABLE:", table)
            continue

        existing = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})")
        }

        missing = columns - existing

        if missing:
            print("MISSING COLUMNS:", table, ", ".join(sorted(missing)))

    conn.close()

print("\nDatabase audit completed.")
