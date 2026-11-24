
import sqlite3
from pathlib import Path

database_dir = Path(__file__).resolve().parent / "databases"

for db_path in sorted(database_dir.glob("*.db")):
    print(f"\n{'=' * 55}")
    print(f"Database: {db_path.name}")

    try:
        connection = sqlite3.connect(
            f"file:{db_path.resolve().as_posix()}?mode=ro",
            uri=True,
        )

        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()

        for (table_name,) in tables:
            try:
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
                print(f"  {table_name}: {count} rows")
            except sqlite3.Error as error:
                print(f"  {table_name}: ERROR - {error}")

        connection.close()

    except sqlite3.Error as error:
        print(f"Could not read database: {error}")
