import sqlite3
import os

paths = [
    r"databases\mobile_store_new.db",
    os.path.join(
        os.environ["LOCALAPPDATA"],
        "MobileStoreERP",
        "databases",
        "mobile_store_new.db"
    )
]

for path in paths:
    print("\nDATABASE:", path)

    con = sqlite3.connect(path)

    columns = [
        row[1]
        for row in con.execute(
            "PRAGMA table_info(store_settings)"
        ).fetchall()
    ]

    print("Store settings columns:", columns)

    if "app_launch_count" in columns:
        result = con.execute(
            "SELECT app_launch_count FROM store_settings"
        ).fetchall()

        print("Launch count:", result)

    con.close()

input("\nPress Enter to close...")