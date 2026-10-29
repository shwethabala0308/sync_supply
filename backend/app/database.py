from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import get_settings

settings = get_settings()

is_sqlite = settings.DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

# Connection pooling (handoff guide, section D): sensible defaults for a
# free-tier Postgres instance — small pool, recycled before a typical
# managed-Postgres idle-connection timeout, and a bounded connect timeout
# so a dead DB fails fast instead of hanging the whole request.
pool_kwargs = {} if is_sqlite else {"pool_size": 5, "max_overflow": 5, "pool_recycle": 280, "pool_timeout": 10}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True, **pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

if is_sqlite:
    # SQLite does not enforce foreign keys (including ON DELETE CASCADE) by
    # default — it has to be turned on per connection. Without this, the
    # cascade behavior declared on the models would silently work in
    # Postgres but silently NOT work in local/dev SQLite, which is exactly
    # the kind of dev/prod divergence that hides a bug until production.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
