
import sqlite3
import secrets
from pathlib import Path
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash


DB_PATH = Path("databases/mobile_store_new.db")

EXPECTED_ERRORS = {
    ("audit_logs", 2, "users", 0),
    ("audit_logs", 4, "users", 0),
    ("audit_logs", 5, "users", 0),
    ("audit_logs", 6, "users", 0),
    ("audit_logs", 7, "users", 0),
    ("sales", 1, "users", 0),
    ("stock_movements", 1, "users", 0),
}

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")

try:
    # 1. Confirm the database has exactly the expected errors.
    errors_before = {
        tuple(row) for row in conn.execute("PRAGMA foreign_key_check")
    }

    if errors_before != EXPECTED_ERRORS:
        raise RuntimeError(
            "Foreign-key errors differ from the expected seven. "
            "Repair cancelled. No database changes made."
        )

    # 2. Confirm IDs 1 and 2 are missing.
    existing = conn.execute(
        "SELECT id FROM users WHERE id IN (1, 2)"
    ).fetchall()

    if existing:
        raise RuntimeError(
            "User ID 1 or 2 already exists. Repair cancelled."
        )

    # 3. Ensure placeholder usernames are available.
    for username in ("legacy_user_1", "legacy_user_2"):
        found = conn.execute(
            "SELECT id FROM users WHERE lower(username) = lower(?)",
            (username,)
        ).fetchone()

        if found:
            raise RuntimeError(
                f"Username {username} already exists. Repair cancelled."
            )

    # 4. Snapshot the historical records we must preserve.
    snapshots = {
        "audit_logs": conn.execute(
            "SELECT * FROM audit_logs WHERE id IN (2,4,5,6,7)"
        ).fetchall(),
        "sales": conn.execute(
            "SELECT * FROM sales WHERE id = 1"
        ).fetchall(),
        "stock_movements": conn.execute(
            "SELECT * FROM stock_movements WHERE id = 1"
        ).fetchall(),
    }

    # 5. Create a separate backup before modifying anything.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(
        f"mobile_store_new_before_legacy_fk_repair_{timestamp}.db"
    )

    with sqlite3.connect(backup_path) as backup:
        conn.backup(backup)

    print("Backup created:", backup_path)

    # 6. Insert only the two missing historical user records.
    now = datetime.now(timezone.utc).isoformat(
        sep=" ", timespec="microseconds"
    )

    conn.execute("BEGIN IMMEDIATE")

    for user_id in (1, 2):
        username = f"legacy_user_{user_id}"
        full_name = (
            f"Historical account {user_id} "
            "(identity unavailable)"
        )

        # Random password is never displayed or stored in plain text.
        password_hash = generate_password_hash(
            secrets.token_urlsafe(48)
        )

        conn.execute(
            """
            INSERT INTO users (
                id, username, full_name, password_hash,
                role, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'legacy', 0, ?, ?)
            """,
            (
                user_id,
                username,
                full_name,
                password_hash,
                now,
                now,
            ),
        )

    # 7. Verify foreign keys before committing.
    errors_after = list(
        conn.execute("PRAGMA foreign_key_check")
    )

    if errors_after:
        raise RuntimeError(
            f"Foreign-key errors remain: {errors_after}"
        )

    # 8. Verify original historical records are unchanged.
    current_snapshots = {
        "audit_logs": conn.execute(
            "SELECT * FROM audit_logs WHERE id IN (2,4,5,6,7)"
        ).fetchall(),
        "sales": conn.execute(
            "SELECT * FROM sales WHERE id = 1"
        ).fetchall(),
        "stock_movements": conn.execute(
            "SELECT * FROM stock_movements WHERE id = 1"
        ).fetchall(),
    }

    if snapshots != current_snapshots:
        raise RuntimeError(
            "Historical records changed unexpectedly. "
            "Transaction cancelled."
        )

    # 9. Check database integrity before committing.
    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity != "ok":
        raise RuntimeError(
            f"Database integrity check failed: {integrity}"
        )

    conn.commit()

    print("\nREPAIR SUCCESSFUL")
    print("Restored historical user IDs: 1 and 2")
    print("Both accounts are inactive.")
    print("Historical sales, stock and audit records preserved.")
    print("Foreign-key errors remaining: 0")
    print("Integrity check:", integrity)

except Exception:
    conn.rollback()
    raise

finally:
    conn.close()
