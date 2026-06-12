# Outly (Python)

Python port of [Outly](https://github.com/aniket1251/outly) — a cold-outreach email platform for job seekers. The original TypeScript/Express/Prisma/BullMQ backend is reimplemented with FastAPI, SQLAlchemy 2.0 (async), and arq, structured as a hexagonal architecture.

## Architecture

Dependencies point inward only: `api`/`worker` → `application` → `domain`. Adapters implement application ports.

```
src/outly/
  domain/        Pure business logic. No I/O, no framework imports.
  application/   Use cases and services. Depends on domain + ports (Protocols).
  adapters/      Infrastructure: SQLAlchemy repos, SMTP, JWT/AES, arq, storage.
  api/           FastAPI app, routes, DI wiring. HTTP concerns only.
  worker/        arq worker: send pipeline, cron maintenance jobs.
  config.py      Pydantic settings, env-driven.
```

Each package has its own CLAUDE.md with specifics.

## Key conventions

- Ports are `typing.Protocol` classes in `application/ports.py`; adapters implement them structurally (no inheritance).
- All datetimes are UTC-aware in code; the DB layer normalizes naive values (SQLite) back to UTC on read.
- Domain entities are plain dataclasses; SQLAlchemy rows (`*Row` in `adapters/db/models.py`) map to them by field name via `to_entity`/`apply_entity`.
- API JSON is camelCase (parity with the original Express API) — `api/serialization.py` converts dataclasses recursively.
- Services raise `application.errors.AppError` subclasses; a FastAPI exception handler maps them to `{"message": ...}` responses.
- Optimistic concurrency everywhere state races matter: `UPDATE ... WHERE id = ? AND status = ?` and check rowcount (campaign transitions, email-job claims, sequence-step advancement).
- Status enums are `StrEnum`s; DB stores plain strings, comparisons work both ways.

## Behavioral parity with the original

The port preserves the original's exact behavior: route paths, status codes, error message strings, state machines, throttle limits/thresholds, warmup ramp `[20..500]` over 14 days, scheduling jitter ranges (±20%/±40%), cooldown after 3 consecutive errors, and the worker recovery sweeps. When changing behavior, check `API_REFERENCE.md` in the original repo first.

Known intentional deviations:
- Cloudinary replaced by a `FileStorage` port with a local-filesystem adapter (`/files` static mount).
- BullMQ/Redis replaced by arq (still Redis); when Redis is down the API degrades to a logging no-op queue.
- No Alembic: `init_db` runs `create_all` on startup (SQLite for local dev, PostgreSQL via asyncpg in prod).

## Commands

```
uv sync                 # install
uv run pytest           # tests (SQLite, no Redis needed)
uv run uvicorn outly.api.app:app --reload   # API on :8000
uv run arq outly.worker.main.WorkerSettings # worker (needs Redis)
```
