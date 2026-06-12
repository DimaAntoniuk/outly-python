# domain

Pure business logic. No I/O, no async, no imports from other outly packages. Everything here is unit-testable without a database.

- `entities.py` — dataclasses mirroring the persistence schema (User, Sender, EmailCampaign, EmailJob, SequenceStep, RecipientSequenceState, ...). Field names are snake_case versions of the original Prisma model fields.
- `enums.py` — `CampaignStatus`, `EmailStatus`, `StepStatus`, `TrackingEventType` as `StrEnum`s.
- `state_machine.py` — campaign status transition table. SCHEDULED→{SENDING,PAUSED,CANCELLED}, SENDING→{PAUSED,CANCELLED,COMPLETED}, PAUSED→{SENDING,CANCELLED,COMPLETED}; CANCELLED/COMPLETED are terminal. Terminal email statuses: SENT/FAILED/CANCELLED.
- `templating.py` — `{{variable}}` parsing/resolution; lookups are case-insensitive, unknown tokens are left intact.
- `preprocessing.py` — click-link rewriting (skips mailto:/#/already-tracked) then open-pixel injection (before `</body>` or appended).
- `scheduling.py` — send-time math: `compute_send_offsets` spaces recipients by `3600/(hourlyLimit*senders)` with ±40% jitter, or by `delaySeconds` with ±20% jitter when that's larger; `compute_jittered_delay` is ±30% with a 1s floor (used for retry delays).
- `rotation.py` — round-robin sender assignment honoring per-sender daily limits; assignment list may be shorter than the recipient count when all limits are hit.
- `validation.py` — sequence steps (max 5 follow-ups, waitDays ≥ 1), search query (≤200 chars), date-range/date-field checks. Mirrors original error messages.
