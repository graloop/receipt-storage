# The connection to the SQLite file, and the session each request works with.

import json
import os
import unicodedata

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()
os.makedirs(settings.data_dir, exist_ok=True)

engine = create_engine(
    f"sqlite:///{os.path.join(settings.data_dir, 'receipts.db')}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def fold(value: str | None) -> str:
    """Lowercase, no accents: searching "epicerie" finds "Épicerie" and the other way around."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def item_names(items_json: str | None) -> str:
    """The item names only: searching "price" shouldn't match every receipt's JSON."""
    return " ".join(str(item.get("name", "")) for item in json.loads(items_json or "[]"))


@event.listens_for(engine, "connect")
def _on_connect(connection, _record):
    # SQLite only enforces foreign keys when asked, on each connection.
    connection.execute("PRAGMA foreign_keys=ON")
    # Makes the two functions above usable inside SQL, for the search.
    connection.create_function("fold", 1, fold, deterministic=True)
    connection.create_function("item_names", 1, item_names, deterministic=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def add_missing_columns() -> None:
    """Tiny forward-only migration, so a new version keeps the receipts already on
    the server: create_all() never alters existing tables, so add any *nullable*
    column a model gained since the table was created."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))
