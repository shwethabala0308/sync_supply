"""
Pre-flight schema check run by the container entrypoint (see Dockerfile)
before uvicorn starts serving traffic.

This does NOT run migrations — see Dockerfile/render.yaml for why migration
ownership is kept to exactly one place (Render's preDeployCommand). It only
confirms the database's current Alembic revision matches the code's latest
migration, and exits non-zero (failing the container, so the platform never
routes traffic to it) if it doesn't. SQLite (local/dev, schema managed by
`Base.metadata.create_all` in app.main) is skipped entirely.
"""
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.database import is_sqlite


def main() -> int:
    settings = get_settings()

    if is_sqlite:
        print("verify_schema: SQLite detected (dev/demo) — schema is managed by "
              "create_all() at app startup, not Alembic. Skipping.")
        return 0

    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    head_revision = script.get_current_head()

    engine = create_engine(settings.DATABASE_URL)
    try:
        with engine.connect() as conn:
            has_table = conn.execute(
                text("SELECT to_regclass('public.alembic_version')")
            ).scalar()
            if not has_table:
                print(
                    "verify_schema: FAILED — no alembic_version table found. "
                    "Migrations have not been run against this database yet. "
                    "Run `alembic upgrade head` (Render's preDeployCommand does this "
                    "automatically on every deploy — check the pre-deploy step's logs "
                    "if this is unexpected).",
                    file=sys.stderr,
                )
                return 1
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()

    if current != head_revision:
        print(
            f"verify_schema: FAILED — database is at revision {current!r}, code expects "
            f"{head_revision!r}. The pre-deploy migration step did not complete, or this "
            "image is older/newer than the schema it's pointed at.",
            file=sys.stderr,
        )
        return 1

    print(f"verify_schema: OK — database schema is at {current!r}, matching code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
