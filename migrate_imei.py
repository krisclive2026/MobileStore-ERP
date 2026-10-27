
import sqlite3
from pathlib import Path

DB_PATH = Path("databases/mobile_store_new.db")


def migrate():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH.resolve()}"
        )

    con = sqlite3.connect(DB_PATH, timeout=10)

    try:
        con.execute("PRAGMA busy_timeout = 10000")
        con.execute("PRAGMA foreign_keys = OFF")

        con.execute("BEGIN IMMEDIATE")

        columns = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(imei_records)"
            ).fetchall()
        }

        required = {
            "id", "product_id", "status",
            "sale_id", "created_at", "updated_at"
        }

        if not required.issubset(columns):
            raise RuntimeError(
                "Unexpected imei_records schema. "
                f"Found columns: {sorted(columns)}"
            )

        if "imei1" not in columns:
            con.execute(
                "ALTER TABLE imei_records ADD COLUMN imei1 VARCHAR(15)"
            )

        if "imei2" not in columns:
            con.execute(
                "ALTER TABLE imei_records ADD COLUMN imei2 VARCHAR(15)"
            )

        columns = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info(imei_records)"
            ).fetchall()
        }

        # Preserve legacy single-IMEI values.
        if "imei" in columns:
            con.execute("""
                UPDATE imei_records
                SET imei1 = imei
                WHERE (imei1 IS NULL OR TRIM(imei1) = '')
                  AND imei IS NOT NULL
                  AND TRIM(imei) <> ''
            """)

        # Do not discard or guess missing IMEIs.
        missing = con.execute("""
            SELECT id
            FROM imei_records
            WHERE imei1 IS NULL OR TRIM(imei1) = ''
            LIMIT 10
        """).fetchall()

        if missing:
            raise RuntimeError(
                "Records without IMEI1 found: "
                f"{[r[0] for r in missing]}. "
                "No changes committed."
            )

        # Ensure no duplicate IMEI exists across either slot.
        duplicate = con.execute("""
            SELECT a.id, b.id
            FROM imei_records a
            JOIN imei_records b ON a.id < b.id
            WHERE a.imei1 = b.imei1
               OR a.imei1 = b.imei2
               OR a.imei2 = b.imei1
               OR (
                   a.imei2 IS NOT NULL
                   AND a.imei2 = b.imei2
               )
            LIMIT 10
        """).fetchall()

        if duplicate:
            raise RuntimeError(
                f"Conflicting IMEI records: {duplicate}. "
                "No changes committed."
            )

        # Preserve record IDs, product/sale links and timestamps.
        old_count = con.execute(
            "SELECT COUNT(*) FROM imei_records"
        ).fetchone()[0]

        con.execute("""
            CREATE TABLE imei_records_new (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                imei1 VARCHAR(15) NOT NULL UNIQUE,
                imei2 VARCHAR(15) UNIQUE,
                status VARCHAR(20) NOT NULL DEFAULT 'in_stock',
                sale_id INTEGER,
                created_at DATETIME,
                updated_at DATETIME,
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (sale_id) REFERENCES sales(id)
            )
        """)

        con.execute("""
            INSERT INTO imei_records_new (
                id, product_id, imei1, imei2,
                status, sale_id, created_at, updated_at
            )
            SELECT
                id, product_id, imei1,
                NULLIF(imei2, ''),
                status, sale_id, created_at, updated_at
            FROM imei_records
        """)

        new_count = con.execute(
            "SELECT COUNT(*) FROM imei_records_new"
        ).fetchone()[0]

        if old_count != new_count:
            raise RuntimeError("Record count mismatch.")

        con.execute("DROP TABLE imei_records")
        con.execute(
            "ALTER TABLE imei_records_new RENAME TO imei_records"
        )

        con.execute("""
            CREATE INDEX ix_imei_records_product_id
            ON imei_records(product_id)
        """)
        con.execute("""
            CREATE INDEX ix_imei_records_imei1
            ON imei_records(imei1)
        """)
        con.execute("""
            CREATE INDEX ix_imei_records_imei2
            ON imei_records(imei2)
        """)

        # Validate this table, not unrelated legacy tables.
        errors = con.execute(
            "PRAGMA foreign_key_check(imei_records)"
        ).fetchall()

        if errors:
            raise RuntimeError(
                f"IMEI table foreign-key errors: {errors}"
            )

        con.commit()

        print("IMEI migration completed successfully.")
        print(f"Records preserved: {new_count}")
        print("Legacy imei column removed.")
        print("New imei1 and imei2 constraints applied.")

    except Exception:
        con.rollback()
        raise

    finally:
        con.close()


if __name__ == "__main__":
    migrate()
