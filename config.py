import os
import sys

from pathlib import Path
import secrets

# Find the application resources
if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(
        getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)
    )
    DATA_DIR = Path(
        os.environ.get("LOCALAPPDATA", str(Path.home()))
    ) / "MobileStoreERP"
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    DATA_DIR = RESOURCE_DIR

# Writable database location
DATABASE_DIR = DATA_DIR / "databases"
DATABASE_DIR.mkdir(parents=True, exist_ok=True)

BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DATABASE_FILE = DATABASE_DIR / "mobile_store_new.db"

if getattr(sys, "frozen", False):
    DATABASE_FILE = DEFAULT_DATABASE_FILE
else:
    DATABASE_FILE = Path(
        os.environ.get(
            "MOBILESTORE_DATABASE_FILE",
            str(DEFAULT_DATABASE_FILE)
        )
    )

DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Persistent secret key for login sessions
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"


def get_secret_key():
    configured_key = os.environ.get("SECRET_KEY")

    if configured_key:
        return configured_key

    if SECRET_KEY_FILE.exists():
        existing_key = SECRET_KEY_FILE.read_text(
            encoding="utf-8"
        ).strip()

        if existing_key:
            return existing_key

    new_key = secrets.token_hex(32)

    SECRET_KEY_FILE.write_text(
        new_key,
        encoding="utf-8"
    )

    return new_key


# Persistent upload folder in packaged EXE mode
UPLOAD_FOLDER = (
    DATA_DIR / "store_logos"
    if getattr(sys, "frozen", False)
    else None
)


class Config:
    SECRET_KEY = get_secret_key()

    UPLOAD_FOLDER = UPLOAD_FOLDER

    SQLALCHEMY_DATABASE_URI = (
        "sqlite:///" + DATABASE_FILE.resolve().as_posix()
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = (
        os.environ.get("COOKIE_SECURE", "false").lower() == "true"
    )

    SQLALCHEMY_ENGINE_OPTIONS = {
    "connect_args": {
        "timeout": 30,
    }
}
