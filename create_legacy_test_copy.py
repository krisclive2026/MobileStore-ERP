
import sqlite3
from pathlib import Path

source_path = Path("databases/mobile_store.db")
target_path = Path("databases/legacy_migration_test.db")

if not source_path.exists():
    raise FileNotFoundError(source_path)

if target_path.exists():
    raise FileExistsError(
        "legacy_migration_test.db already exists. "
        "Do not overwrite it."
    )

source = sqlite3.connect(source_path)
target = sqlite3.connect(target_path)

try:
    source.backup(target)

    result = target.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    print("Legacy test copy created.")
    print("Integrity:", result)
    print("Location:", target_path.resolve())

    if result != "ok":
        raise RuntimeError("Database integrity check failed.")

finally:
    target.close()
    source.close()
