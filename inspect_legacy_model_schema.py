
import sqlite3
from pathlib import Path

from models import db
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite

DB_PATH = Path("databases/legacy_migration_test.db")

conn = sqlite3.connect(DB_PATH)

print("=" * 65)
print("CURRENT MODEL SCHEMA VS LEGACY DATABASE")
print("=" * 65)

for table_name, table in db.metadata.tables.items():

    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    ).fetchone()

    if exists:
        existing_columns = {
            row[1]
            for row in conn.execute(
                f'PRAGMA table_info("{table_name}")'
            )
        }

        missing_columns = [
            column for column in table.columns
            if column.name not in existing_columns
        ]

        if missing_columns:
            print(f"\nTABLE: {table_name}")

            for column in missing_columns:
                print(
                    f"  {column.name}: "
                    f"type={column.type}, "
                    f"nullable={column.nullable}, "
                    f"default={column.default.arg if column.default else None}, "
                    f"foreign_keys="
                    f"{[fk.target_fullname for fk in column.foreign_keys]}"
                )

    else:
        print(f"\nMISSING TABLE: {table_name}")

        sql = CreateTable(table).compile(
            dialect=sqlite.dialect()
        )
        print(sql)

conn.close()

print("\nInspection completed.")
