# adapters

Infrastructure implementations of `application/ports.py`. Implementations are structural (duck-typed against Protocols) — no inheritance.

- `db/models.py` — SQLAlchemy 2.0 declarative rows (`*Row`). Mirrors the original Prisma schema including unique constraints and indexes. JSON columns hold `column_data`, `step_statuses`, `daily_limits`.
- `db/repositories.py` — one `Sql*Repository` per port. Rows map to domain dataclasses by matching field names (`to_entity`/`apply_entity`); naive datetimes from SQLite are normalized to UTC-aware on read. Conditional updates (`update_status_if`, `claim_pending`, `advance_step`) use `UPDATE ... WHERE` and return rowcount-derived bools.
- `db/session.py` — engine/session factory creation + `init_db` (create_all; no migrations).
- `db/seed.py` — idempotent provider-profile seeding (Gmail, Outlook, `*` wildcard default: 10/min, 100/hr, 500/day).
- `security.py` — `AesCredentialCipher`: AES-256-CBC, output `iv_hex:cipher_hex`, wire-compatible with the original Node implementation (PKCS7). `JwtTokenSigner`: HS256, duration strings like `15m`/`30d`, adds `iat`+`jti` so rotated tokens are always unique.
- `smtp.py` — aiosmtplib mailer; implicit TLS when port 465; `verify_credentials` mirrors nodemailer's `transporter.verify()` (connect + login).
- `google.py` — Google ID-token verification via google-auth, run in a thread (the lib is sync).
- `storage.py` — `LocalFileStorage`: saves under `ATTACHMENT_DIR` as `{epoch_ms}-{filename}`, returns `{SERVER_BASE_URL}/files/{name}`; `read()` resolves own URLs from disk and falls back to HTTP for foreign URLs. Swap with an S3/Cloudinary adapter by implementing `FileStorage`.
- `queue.py` — `ArqEmailQueue.enqueue_send(job_id, delay_ms)`; arq job ids are `{email_job_id}-{uuid}` so requeues never collide.
