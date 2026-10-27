
from app import app
from models import db


def initialize_database():
    with app.app_context():
        db.create_all()

        print("Database tables checked successfully.")
        print("Existing tables and records have been preserved.")
        print("No administrator account was created.")


if __name__ == "__main__":
    initialize_database()
