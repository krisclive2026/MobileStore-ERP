
import sqlite3
from pathlib import Path

source = Path("databases/mobile_store_new_before_exe.db")
target = Path("databases/migration_test.db")

if not source.exists():
    raise FileNotFoundError(f"Source database not found: {source}")

if target.exists():
    raise FileExistsError(
        f"{target} already exists. Rename or remove the test copy first."
    )

src = sqlite3.connect(source)
dst = sqlite3.connect(target)

try:
    src.backup(dst)

    result = dst.execute("PRAGMA integrity_check").fetchone()[0]

    if result != "ok":
        raise RuntimeError(f"Database integrity check failed: {result}")

    print("Test database created successfully.")
    print("Source:", source.resolve())
    print("Test copy:", target.resolve())
    print("Integrity check:", result)

finally:
    dst.close()
    src.close()
