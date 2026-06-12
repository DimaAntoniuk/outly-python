# application

Use cases and orchestration. Depends only on `domain` and the port Protocols defined here. Never imports adapters, FastAPI, or SQLAlchemy.

- `ports.py` — every dependency boundary as a `Protocol`: 15 repositories plus `EmailQueue`, `Mailer`, `CredentialCipher`, `TokenSigner`, `GoogleVerifier`, `FileStorage`.
- `errors.py` — `AppError(status_code, message)` hierarchy; the API layer maps these to HTTP responses, so services never know about HTTP frameworks but still control status codes and message strings.
- `throttling.py` — the sending-rate engine, kept faithful to the original:
  - `WarmupEvaluator`: day-N limit from a 14-day ramp `[20,30,50,...,500]`; None when opted out/inactive/finished.
  - `AdaptiveThrottle`: 1-hour rolling error/bounce rates (thresholds 10%/5% → rate multiplier 0.5), cooldown after 3 consecutive errors (default 5 min).
  - `ThrottleEngine`: effective per-minute/hour/day limits = min(provider, sender, warmup) × multiplier (floor 1); `can_send` checks cooldown → minute → hour → day and returns a retry-after; `earliest_resume_time` finds when any pool sender regains capacity.
- `campaigns.py` — `CampaignService.create` is the heart: validates payload (original error strings), dedupes recipients case-insensitively, validates 25 MB attachment total, builds the sender pool + round-robin assignments, computes jittered schedule times, creates sequence step 0 + recipient states, and returns an `enqueue_plan` of `(job_id, delay_ms)`. Callers must commit the session **before** enqueueing (see api/routes/campaigns.py).
- `delivery.py` — `SendEmailUseCase`: the worker pipeline (fetch → state checks → idempotency → atomic claim → sender resolution → daily-capacity/pool failover → throttle check with jittered requeue → decrypt → attachments → template resolution → tracking preprocessing → SMTP send → status + sequence-step updates → completion check). Campaign auto-pauses with `pause_reason="ALL_SENDERS_EXHAUSTED"` when the whole pool is at its daily limit.
- `maintenance.py` — `SequenceSchedulerUseCase` (advances recipients whose waitDays elapsed; atomic `advance_step` guards double-scheduling; sender chosen by `step_number % pool`), `AutoResumeUseCase` (resumes exhausted-paused campaigns once capacity returns; piggybacks rate-counter and refresh-token cleanup), `SweepUseCase` (orphaned/stale SENDING job recovery, stuck campaign completion).
- Per-aggregate services (`auth`, `senders`, `emails`, `sequences`, `templates`, `attachments`, `tracking`) are thin: validate, enforce ownership, delegate to repos.

Invariant: anything that must survive concurrent workers uses conditional UPDATE through a repo method returning bool — never read-modify-write.
