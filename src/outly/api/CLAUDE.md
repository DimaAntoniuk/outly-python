# api

FastAPI layer. HTTP concerns only — parsing, auth, cookies, response shaping. All business rules live in `application`.

- `app.py` — `create_app(settings, queue)`: lifespan creates engine, runs `init_db` + provider seeding, wires adapters onto `app.state`, and connects arq (falling back to a logging `NullEmailQueue` when Redis is unreachable so the API still serves reads). Tests pass a fake queue. Routes mount at the original Express paths; `/files` serves local attachments.
- `deps.py` — per-request DI: `get_services` opens a session, builds all repos/services (`build_services`), commits on success / rolls back on error. `get_current_user` replicates the original middleware exactly (401 messages: "Authorization header missing", "Invalid authorization format", "Invalid or expired token").
- `serialization.py` — recursive dataclass→camelCase JSON conversion for response parity with the Prisma-shaped API.
- `routes/` — one module per Express route file. Request bodies are read as raw dicts (not Pydantic models) so validation errors return the original 400 messages instead of FastAPI's 422s.

Ordering invariant: campaign create/resume must `session.commit()` **before** enqueueing jobs, otherwise the worker can race an uncommitted transaction. The enqueue plans returned by `CampaignService` exist for this.

Cookie contract: refresh token in an httpOnly `refreshToken` cookie scoped to `/auth/refresh`, SameSite=strict, 30-day max-age, `secure` only when `ENV=production`.
