
import sqlite3
from pathlib import Path

files = [
    "migration_test_before_schema.db",
    "migration_test.db",
]

for filename in files:
    path = Path("databases") / filename

    print("\n" + "=" * 55)
    print("DATABASE:", filename)

    conn = sqlite3.connect(path)

    errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    if not errors:
        print("No foreign-key errors.")
    else:
        print("Foreign-key errors:", len(errors))

        for table, rowid, parent, fk_id in errors:
            print(
                f"Table: {table}, "
                f"Row: {rowid}, "
                f"Missing parent: {parent}, "
                f"FK ID: {fk_id}"
            )

    conn.close()

print("\nCheck completed.")
