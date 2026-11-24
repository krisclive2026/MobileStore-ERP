
import sqlite3
from pathlib import Path

before_path = Path(
    "databases/startup_test_before_schema_migration_20261110_173309.db"
)
after_path = Path("databases/startup_test.db")

before = sqlite3.connect(before_path)
after = sqlite3.connect(after_path)

def get_tables(conn):
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

def get_columns(conn, table):
    return {
        row[1]
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    }

def get_rows(conn, table, columns):
    info = conn.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall()

    primary_keys = [
        row[1] for row in sorted(
            [r for r in info if r[5] > 0],
            key=lambda r: r[5]
        )
    ]

    order = primary_keys or ["rowid"]
    selected = ", ".join(f'"{c}"' for c in columns)
    ordering = ", ".join(f'"{c}"' for c in order)

    return conn.execute(
        f'SELECT {selected} FROM "{table}" ORDER BY {ordering}'
    ).fetchall()

try:
    before_tables = get_tables(before)
    after_tables = get_tables(after)

    differences = []

    for table in sorted(before_tables & after_tables):
        common_columns = sorted(
            get_columns(before, table) &
            get_columns(after, table)
        )

        if not common_columns:
            continue

        old_rows = get_rows(before, table, common_columns)
        new_rows = get_rows(after, table, common_columns)

        if old_rows != new_rows:
            differences.append(table)
            print(f"DATA DIFFERENCE: {table}")
        else:
            print(f"UNCHANGED: {table}")

    print("\n" + "=" * 50)

    if differences:
        print("RESULT: DATA DIFFERENCES FOUND")
        print("Tables:", differences)
    else:
        print("RESULT: ALL EXISTING DATA VALUES MATCH")

finally:
    before.close()
    after.close()
