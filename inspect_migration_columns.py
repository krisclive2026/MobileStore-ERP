
from models import db
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import sqlite

tables_to_inspect = [
    "store_settings",
    "service_jobs",
    "service_parts",
    "imei_records",
]

for table_name in tables_to_inspect:
    table = db.metadata.tables[table_name]

    print("\n" + "=" * 60)
    print("TABLE:", table_name)

    for column in table.columns:
        print(
            f"\nColumn: {column.name}",
            f"\n  Type: {column.type}",
            f"\n  Nullable: {column.nullable}",
            f"\n  Default: {column.default}",
            f"\n  Server default: {column.server_default}",
            f"\n  Foreign keys: {[str(fk.target_fullname) for fk in column.foreign_keys]}",
        )

print("\n" + "=" * 60)
print("SERVICE_STOCK CREATE TABLE SQL:")
print(
    CreateTable(db.metadata.tables["service_stock"])
    .compile(dialect=sqlite.dialect())
)
