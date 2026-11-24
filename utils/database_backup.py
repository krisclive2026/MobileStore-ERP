
import sqlite3
from datetime import datetime
from pathlib import Path

from config import DATABASE_FILE, BACKUP_DIR


def create_database_backup(category="manual"):
    """
    Create a consistent SQLite database backup.
    Returns the backup file path.
    """

    source_path = Path(DATABASE_FILE)

    if not source_path.exists():
        raise FileNotFoundError(
            f"Database not found: {source_path}"
        )

    backup_dir = Path(BACKUP_DIR) / category
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    backup_path = backup_dir / (
        f"{source_path.stem}_{category}_{timestamp}.db"
    )

    source_conn = sqlite3.connect(
        source_path,
        timeout=30
    )

    backup_conn = sqlite3.connect(
        backup_path,
        timeout=30
    )

    try:
        # Use SQLite's backup API for a consistent backup.
        source_conn.backup(backup_conn)

        # Verify the backup.
        integrity = backup_conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                f"Backup integrity check failed: {integrity}"
            )

    except Exception:
        backup_conn.close()
        source_conn.close()

        if backup_path.exists():
            backup_path.unlink()

        raise

    else:
        backup_conn.close()
        source_conn.close()

    return backup_path


def validate_database_backup(backup_path):
    """
    Validate a backup before restoring it.
    Returns (True, message) if valid, otherwise (False, message).
    """

    backup_path = Path(backup_path)

    if not backup_path.is_file():
        return False, "Backup file does not exist."

    connection = None

    try:
        connection = sqlite3.connect(
            str(backup_path),
            timeout=30
        )

        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()

        if not integrity or integrity[0] != "ok":
            return False, "Backup database integrity check failed."

        required_tables = {
            "users",
            "products",
            "imei_records",
            "used_handset_sellers",
            "customers",
            "suppliers",
            "purchases",
            "purchase_items",
            "sales",
            "sale_items",
            "payments",
            "stock_movements",
            "expenses",
            "audit_logs",
            "store_settings",
            "service_jobs",
            "service_parts",
            "service_history",
            "service_stock",
        }

        existing_tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            ).fetchall()
        }

        missing_tables = required_tables - existing_tables

        if missing_tables:
            return False, (
                "Backup is missing required tables: "
                + ", ".join(sorted(missing_tables))
            )

        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            return False, "Backup contains foreign-key errors."

        return True, "Backup validated successfully."

    except sqlite3.Error:
        return False, "The selected file is not a valid SQLite database."

    finally:
        if connection is not None:
            connection.close()

