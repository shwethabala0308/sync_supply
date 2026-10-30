FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

# Migration ownership: exactly one thing runs `alembic upgrade head` against
# a deployment — Render's preDeployCommand (see render.yaml), which Render
# runs to completion, once, before any new instance is started or traffic is
# routed to it. Running migrations again here in CMD would let a second
# instance (e.g. during a rolling deploy, or a manual restart that re-uses
# this image outside Render's pre-deploy step) race the first one against
# the same database.
#
# What this entrypoint does instead is *verify* the schema is current before
# serving traffic, so a misconfigured deployment (migrations skipped, wrong
# DATABASE_URL, pre-deploy step failed silently) fails fast and loud instead
# of serving requests against a stale/partial schema. `app.main`'s startup
# event performs the equivalent table-presence check for safety in all
# environments; this is a cheaper pre-flight that avoids even booting
# uvicorn when migrations are clearly behind.
#
# $PORT is set by most free-tier hosts (Render, Railway); default to 8000
# locally.
CMD ["sh", "-c", "alembic upgrade head && python -m app.verify_schema && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
