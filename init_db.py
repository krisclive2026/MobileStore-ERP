
from app import app
from models import db

def initialize_database():
    with app.app_context():
        db.create_all()
        print("Database tables created successfully.")
        print("No administrator account was created.")

if __name__ == "__main__":
    initialize_database()
