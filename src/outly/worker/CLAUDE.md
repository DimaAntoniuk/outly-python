# worker

arq worker process. Run with `uv run arq outly.worker.main.WorkerSettings` (requires Redis).

- `send_email(ctx, email_job_id)` — the only queued function; delegates to `SendEmailUseCase`. Idempotent: re-delivery of an already-SENT/CANCELLED job is a no-op, and the PENDING→SENDING claim is atomic, so duplicate queue entries are safe.
- Cron jobs (each opens its own session and commits at the end):
  - sequence scheduler — every 15 min (`:00,:15,:30,:45`)
  - auto-resume + cleanup — hourly (`:00`)
  - stale SENDING sweep — every 2 min (jobs stuck > `STALE_SENDING_THRESHOLD_MS`, default 5 min)
  - stuck campaign sweep — every 5 min
- `startup` — initializes DB/adapters into `ctx`, then runs orphaned-job recovery and an initial stuck-campaign sweep before accepting jobs (mirrors the original worker boot).
- Concurrency: `max_jobs` from `WORKER_CONCURRENCY` (default 5); `max_tries=3` for transient failures. Permanent failures (bad credentials, unverified sender, SMTP 5xx) are caught inside the use case and recorded as FAILED — they do not retry.

Each job/cron builds its repos from a fresh session via the helpers in `main.py`; never share sessions across jobs.
