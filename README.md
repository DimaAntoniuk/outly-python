# Outly (Python)

Python port of [Outly](https://github.com/aniket1251/outly) — a cold-outreach email platform for job seekers. Connect Gmail senders, import recipients, write templated emails, and let the engine handle human-like scheduling, sender rotation, warmup, throttling, and follow-up sequences.

Built with **FastAPI**, **SQLAlchemy 2.0 (async)**, and **arq**, in a hexagonal architecture (`domain` → `application` → `adapters`/`api`/`worker`). See `CLAUDE.md` files in each package for design notes.

## Features (ported 1:1 from the original backend)

- Google OAuth login with rotating refresh tokens (httpOnly cookie)
- Senders with SMTP verification, AES-256-CBC encrypted app passwords
- 14-day adaptive warmup ramp per sender
- Throttle engine: provider/sender/warmup limits, adaptive slowdown on errors/bounces, cooldown after consecutive failures
- Campaigns with multi-sender rotation, jittered human-like scheduling, pause/resume/cancel state machine
- Follow-up sequences (up to 5 steps) with a periodic scheduler
- Open/click tracking (pixel + link rewriting) and metrics endpoints
- Worker recovery: orphaned/stale job sweeps, stuck campaign completion, auto-resume of sender-exhausted campaigns

## Quick start

```bash
uv sync
cp .env.example .env          # adjust as needed

# API (SQLite by default; Redis optional for reads)
uv run uvicorn outly.api.app:app --reload

# Worker (requires Redis)
uv run arq outly.worker.main.WorkerSettings

# Tests (no Redis needed)
uv run pytest
```

For local testing without Google OAuth, `POST /auth/dev-login` with `{"email": "me@example.com"}` returns an access token (development only — the route doesn't exist when `ENV=production`).

For PostgreSQL set `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/outly`. Tables are created automatically on startup; provider profiles are seeded idempotently.

## Configuration

All settings are environment variables (see `.env.example`): `DATABASE_URL`, `REDIS_URL`, `ENCRYPTION_KEY` (64 hex chars — generate with `openssl rand -hex 32`), `JWT_ACCESS_SECRET`/`JWT_REFRESH_SECRET`, `ACCESS_TOKEN_EXPIRES`/`REFRESH_TOKEN_EXPIRES` (`15m`, `30d`), `GOOGLE_CLIENT_ID`, `TRACKING_BASE_URL`, `SERVER_BASE_URL`, `ATTACHMENT_DIR`, `WORKER_CONCURRENCY`.

The dev defaults work out of the box but are not production secrets — set real values in production (`ENV=production` also enables secure cookies).

## API

Routes match the original Express server exactly (`/auth`, `/users`, `/senders`, `/campaigns`, `/emails`, `/attachments`, `/templates`, `/campaigns/{id}/sequence`, `/track`, `/api/tracking`). The original `API_REFERENCE.md` applies, with one substitution: attachments are stored on the local filesystem and served from `/files/...` instead of Cloudinary.
