
from app import app
from models import db
from sqlalchemy import inspect, text

with app.app_context():
    engine = db.engine

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("sales")
    }

    if "gst_mode" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE sales "
                    "ADD COLUMN gst_mode VARCHAR(20) "
                    "NOT NULL DEFAULT 'exclusive'"
                )
            )
        print("SUCCESS: gst_mode column added to sales table.")
    else:
        print("INFO: gst_mode column already exists.")
