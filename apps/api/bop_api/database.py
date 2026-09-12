from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


def make_database(url: str):
    """PostgreSQL in the stack; SQLite is explicitly supplied by isolated tests."""
    options = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(url, **options)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(engine, "begin")
        def serialize_sqlite(connection):
            # SQLite has no SELECT FOR UPDATE. Serialize test transactions too.
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    return engine, sessionmaker(engine, expire_on_commit=False)

