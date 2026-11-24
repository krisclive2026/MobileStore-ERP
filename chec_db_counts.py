
import sqlite3
from pathlib import Path

files = [
    "mobile_store_new.db",
    "mobile_store_new_before_exe.db",
    "mobile_store_new_backup_20261103_153816.db",
]

for filename in files:
    path = Path("databases") / filename

    if not path.exists():
        continue

    conn = sqlite3.connect(path)

    print("\n" + "=" * 55)
    print("DATABASE:", filename)
    print("=" * 55)

    tables = conn.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """).fetchall()

    for (table,) in tables:
        count = conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]

        print(f"{table}: {count} records")

    conn.close()

print("\nDatabase count check completed.")
