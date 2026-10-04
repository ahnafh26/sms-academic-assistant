# SMS-First Academic Assistant — Architecture (approved)

"Connect your university once, then text one phone number for your entire academic life."

This product is a reliable academic data system that students control by text. There's no student dashboard: students talk to one shared Twilio number. Web pages exist only for one-off setup tasks (pasting an iCal URL, uploading a syllabus, OAuth, deleting an account). The database and connected sources are the source of truth. The LLM only interprets requests and writes replies, and never supplies facts.

**Current status:** Phase 0, the Brightspace iCal viability spike (`spikes/brightspace_ical/`). Nothing below is built yet.

**Amendments now part of the design:**
- **`refresh_academic_data()` tool:** "refresh", "check brightspace" or "sync my courses" triggers an immediate sync of the user's connections. Rate-limited (e.g. 1 every 10 minutes and 10 a day per user, with in-flight dedupe so a second request joins the running sync). It replies "Checking LEARN…" and then a summary of the changes.
- **Follow-up support in conversation state:** `conversation_state` also stores `last_query` (tool + args, so "and next week?" or "what about tomorrow?" re-runs it with a shifted range), `focus` (the current item or course, so "how much is that worth?", "when is it due?", "remind me about that" and "mark that done" resolve to it), and `last_answer_basis` (the tool results and ranking reasons behind the last reply, so "why?" can be answered from facts).
- **Syllabus confirmation by confidence tier:** high confidence (≥ 0.9) plus a verified source quote plus passing deterministic validation → auto-accepted (listed in the summary so the student can correct it). Medium (0.6–0.9) → ask the student to confirm. Low (< 0.6) → never stored as authoritative, and the student gets an open question. Students don't have to confirm fields that were extracted correctly.

---

## 1. Recommended MVP architecture

**Three processes, one Postgres database, no Redis:**

| Process | Responsibility |
|---|---|
| `web` (FastAPI/uvicorn) | Twilio webhooks, setup/upload pages, health checks. Stateless. |
| `worker` | Claims jobs from a Postgres-backed queue (`FOR UPDATE SKIP LOCKED`): handling inbound messages, iCal syncs, syllabus extraction, outbound sends. |
| `scheduler` (a loop inside the worker process for the MVP) | Runs every 30–60s and enqueues due work: reminders, briefings, periodic syncs, weekly summaries. |

**Key decisions**
- **The inbound webhook returns 200 right away with empty TwiML** and puts the message on a queue. LLM calls and tool loops can take longer than Twilio's 15s timeout, so replies go out through the Twilio REST API from the worker.
- **Use a Postgres queue instead of Redis/Celery.** It's one less service, it's transactional with domain writes, and it handles MVP volume (thousands of users) easily. The queue sits behind a `JobQueue` interface so it can be swapped later.
- **Every outbound message goes through an outbox** (`outbound_messages` with a unique `dedupe_key`). This makes reminders and briefings idempotent: a crash or retry can never send the same text twice.
- **The LLM is never handed the user_id or allowed to choose it.** Tools are bound to the authenticated user server-side.
- **Use deterministic code wherever possible.** Onboarding is a state machine. Opt-out keywords never reach the LLM. Date math, grade math, change detection and normalization are all plain Python.
- Stack: Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + Alembic, Pydantic v2, `icalendar` + `recurring-ical-events`, `httpx`, Twilio SDK, an OpenAI-compatible client behind an `LLMClient` interface (`LLM_BASE_URL`/`LLM_MODEL` env vars), `pypdf`/`pdfplumber` and `python-docx`, `cryptography` (AES-GCM), pytest. Supabase is used as managed Postgres plus Storage, in the Canadian region (`ca-central-1`). The app connects with a normal Postgres DSN, so it isn't locked to Supabase.

## 2. Architecture diagram

```
 Student phone ──SMS/MMS──► Twilio ──POST /webhooks/twilio/sms──► web (FastAPI)
      ▲                                                        │ verify signature
      │                                                        │ dedupe MessageSid
      │                                                        │ insert messages row
      │                                                        ▼
      │                                               jobs: handle_inbound
      │                                                        │
      │                                                   worker ▼
      │                      ┌──────────── Conversation Router ─────────────┐
      │                      │ 1. compliance keywords (STOP/HELP/START)     │
      │                      │ 2. pending confirmation? (YES/NO/codes)      │
      │                      │ 3. onboarding state machine                  │
      │                      │ 4. AI orchestrator (LLM + tools loop)        │
      │                      └──────────────────────┬───────────────────────┘
      │                                             │ tool calls (user-bound)
      │                                             ▼
      │                         Domain services ── Repositories ── PostgreSQL
      │                          (items, reminders,                  ▲
      │                           grades, syllabus)                  │
      │                                                              │ upsert + diff
      │    outbound_messages (outbox) ◄── notifications ◄── change events
      │            │                                                 │
      └── Twilio REST ◄── worker: send_sms                           │
                                                                     │
 Brightspace iCal feed ──► BrightspaceCalendarConnector ──► Normalizer ──► Sync engine
 Brightspace API (later) ──► BrightspaceApiConnector ──┘        (match, resolve sources,
 Syllabus file ──► Storage ──► Extractor (text + LLM JSON) ──►    detect changes)
 Email (later) ──► EmailConnector ──┘

 scheduler tick (every 30–60s): due reminders │ briefings │ periodic syncs │ weekly summaries
```

## 3. Database schema (PostgreSQL)

Conventions: UUID primary keys, every timestamp is `timestamptz` stored in UTC, and local-time concepts (briefing time, quiet hours) are stored as `time` plus an IANA timezone. Every user-owned table has `user_id` with an index. The repository layer always filters by `user_id`, and Postgres RLS can be added later as defense in depth.

```sql
institutions(id, slug UNIQUE, name, lms_type, lms_display_name, base_url, timezone DEFAULT 'America/Toronto',
             aliases text[], connection_methods text[], config jsonb, is_active, created_at)
             -- config: feed URL host pattern, course-code regex overrides, title-suffix rules

users(id, phone_e164 UNIQUE, status: onboarding|active|paused|opted_out|deleted,
      onboarding_step, institution_id FK, preferred_name, timezone,
      consent_at, consent_version, opted_out_at, created_at, deleted_at)

user_preferences(user_id PK FK, briefing_enabled DEFAULT true, briefing_time DEFAULT '08:00',
      evening_alerts_enabled DEFAULT false, evening_alert_time, default_reminder_offset_min NULL,
      weekly_summary_enabled DEFAULT true, quiet_start DEFAULT '23:00', quiet_end DEFAULT '07:00',
      last_briefing_local_date, last_weekly_local_date, updated_at)

lms_connections(id, user_id, institution_id, method: brightspace_api|brightspace_calendar,
      status: pending|active|error|revoked, encrypted_secret bytea, secret_key_version,
      -- iCal URL (it embeds a token, so treat it like a password) or OAuth tokens
      token_expires_at, last_sync_at, last_success_at, consecutive_failures, last_error, created_at)

courses(id, user_id, institution_id, code (e.g. 'CS135'), name, term, external_ids jsonb,
      aliases text[], target_grade numeric, created_at)       UNIQUE(user_id, code, term)

academic_items(id, user_id, course_id NULL, institution_id, title, type (enum per spec),
      description, available_at, start_at, due_at, all_day bool, points_possible, weight,
      status: upcoming|completed|overdue|cancelled,        -- effective (resolved) values
      completion_source: user|lms NULL, completed_at,
      estimated_minutes, difficulty, primary_source, source_url,
      possibly_removed_since, created_at, updated_at, last_synced_at)

source_records(id, user_id, connection_id NULL, source, external_source_id,
      academic_item_id FK NULL, payload jsonb, payload_hash, normalized jsonb,
      first_seen_at, last_seen_at, missing_since)
      UNIQUE(user_id, source, external_source_id)
      -- one row per external record; the per-source fact store used for conflicts

item_fact_overrides(id, user_id, academic_item_id, field, value jsonb,
      source: user|email|syllabus, note, created_at, superseded_at)

conflicts(id, user_id, academic_item_id, field, candidates jsonb, chosen_source,
      status: open|resolved|dismissed, notified_at, resolved_at)

syllabi(id, user_id, course_id, storage_path, mime_type, sha256, status:
      uploaded|extracting|needs_confirmation|confirmed|failed, extracted jsonb,
      extracted_text, extraction_model, created_at, confirmed_at)
syllabus_chunks(id, syllabus_id, user_id, ordinal, heading, content, tsv tsvector)  -- FTS for Q&A
syllabus_policies(id, syllabus_id, user_id, kind: late_penalty|attendance|drop_rule|missed_work|other,
      summary, quote, confidence)

assessment_weights(id, user_id, course_id, syllabus_id NULL, category_name, weight_pct,
      item_count, drop_lowest DEFAULT 0, is_bonus, confidence, confirmed bool, source_quote)
grades(id, user_id, course_id, assessment_weight_id NULL, academic_item_id NULL, label,
      score numeric, out_of numeric DEFAULT 100, source: user|brightspace_api, created_at)

reminders(id, user_id, academic_item_id NULL, kind: absolute|before_due|daily_until_complete,
      fire_at timestamptz, offset_min NULL, local_time NULL, message NULL,
      status: active|done|cancelled, last_fired_at, created_at)

messages(id, user_id NULL, direction: in|out, twilio_sid UNIQUE, body, media jsonb,
      status, error_code, intent, created_at)        -- body retention: 90 days
outbound_messages(id, user_id, dedupe_key UNIQUE, kind: reply|reminder|briefing|change_alert|weekly|system,
      body, status: queued|sending|sent|failed|suppressed, attempts, send_after, twilio_sid, error, created_at)

conversation_state(user_id PK, recent_turns jsonb (last ~8, truncated), last_item_refs jsonb,
      pending_action jsonb (e.g. awaiting syllabus for course X, delete-confirm code), updated_at)

setup_tokens(id, user_id, purpose: connect_calendar|upload_syllabus|oauth|delete_account,
      token_hash UNIQUE, payload jsonb, expires_at, used_at, created_at)

sync_jobs(id, user_id, connection_id, trigger: initial|scheduled|manual, status, started_at,
      finished_at, stats jsonb, error)
sync_events(id, user_id, sync_job_id, academic_item_id, kind: created|due_changed|start_changed|
      title_changed|cancelled|restored|possibly_removed, old jsonb, new jsonb,
      significant bool, notified_at, created_at)

jobs(id, kind, payload jsonb, run_after, status, attempts, max_attempts, locked_by, locked_at,
     last_error, dedupe_key UNIQUE NULL, created_at)      -- Postgres queue
audit_log(id, user_id NULL, actor: user|system|admin, action, detail jsonb, created_at)
```

## 4. Repository structure

```
SMSapp/
  pyproject.toml  alembic.ini  Dockerfile  docker-compose.yml (postgres for dev)  .env.example
  app/
    main.py                       # FastAPI factory
    config.py                     # pydantic-settings; all secrets from env
    api/        webhooks_twilio.py  setup_pages.py  upload_pages.py  health.py  oauth.py (later)
    templates/  connect_calendar.html  upload_syllabus.html  done.html  (tiny single-purpose pages)
    database/   session.py  base.py  migrations/
    models/     # SQLAlchemy ORM, one file per aggregate (user.py, item.py, course.py, ...)
    schemas/    # Pydantic: domain models (NormalizedItem, ToolResult, ...)
    repositories/ # all queries scoped by user_id
    domain/     timeparse.py  item_types.py  course_codes.py
    connectors/
      base.py                     # LMSConnector protocol, RawRecord
      registry.py                 # lms_type -> connector
      brightspace/ calendar_connector.py  api_connector.py (stub)  normalizer.py  institution_rules.py
    sync/       engine.py  matcher.py  source_resolver.py  change_detector.py
    sms/        twilio_client.py  router.py  compliance.py  formatting.py (segment-aware)
    onboarding/ state_machine.py  institution_matcher.py
    ai/         orchestrator.py  llm_client.py  prompts/system.md  tools/ (registry.py + one module per tool group)
    reminders/  service.py  scheduler.py
    notifications/ briefings.py  change_alerts.py  weekly.py  outbox.py  quiet_hours.py
    syllabus/   ingest.py  text_extract.py  llm_extract.py  validate.py  search.py
    grades/     calculator.py (pure)  service.py
    prioritization/ ranker.py
    workers/    queue.py  runner.py  tick.py  handlers.py
    security/   crypto.py (AES-GCM envelope)  tokens.py  twilio_signature.py  rate_limit.py
    seed/       institutions.yaml     # the 13 Ontario schools
  tests/        unit/ (calculator, normalizer, timeparse, change_detector) integration/ fixtures/ics/
```

## 5. Core API endpoints (deliberately minimal)

| Method/Path | Purpose |
|---|---|
| `POST /webhooks/twilio/sms` | Inbound SMS/MMS. Validates `X-Twilio-Signature`, dedupes on `MessageSid`, enqueues the message, returns empty TwiML. |
| `POST /webhooks/twilio/status` | Delivery status callback; updates `outbound_messages` and `messages`. |
| `GET/POST /s/{token}` | Single-purpose "paste your Brightspace calendar URL" page with school-specific instructions and screenshots. |
| `GET/POST /u/{token}` | Single-purpose syllabus upload (PDF/DOCX/image, 15 MB max). |
| `GET /x/{token}` | Account deletion confirmation page (or confirm by SMS code). |
| `GET /privacy`, `/terms` | Static pages linked from the consent text. |
| `GET /healthz`, `/readyz` | Liveness, plus a DB check. |
| `GET /oauth/brightspace/{inst}/start`, `/callback` | Post-MVP, only for partner schools. |
| `/admin/*` | Post-MVP, internal, behind SSO/basic auth. |

Tokens: 32 random bytes, base64url-encoded. Only the SHA-256 hash is stored. They expire after 30 minutes and work once. Each token is scoped to one purpose and one user. When the action completes, the student gets a text back and the page just says "Done, head back to your messages."

## 6. AI tool definitions

The orchestrator injects `user_id`, the user's timezone, the current local datetime, the institution and the course list (codes and aliases) into context. The LLM never passes `user_id`. Each tool returns compact JSON with stable short item refs (`i1`, `i2`, …). Those refs map to UUIDs in `conversation_state.last_item_refs`, which is what lets follow-ups like "mark it done" or "what about tomorrow" work.

| Tool | Args | Returns |
|---|---|---|
| `list_items` | `start_date?, end_date?, course?, types?[], include_completed=false, limit` | Items with ref, course, title, type, local due time, status, weight, estimate. Covers today, tomorrow, this week, and "before Friday". |
| `next_items` | `count=3, types?[]` | The next N incomplete items. |
| `get_exams` | `course?` | Exams and midterms, upcoming first. |
| `get_workload` | `start_date, end_date` | Per-day counts and weights, plus the busiest day. Computed, not guessed. |
| `find_items` | `query` ("econ", "cs a4") | Fuzzy match against course aliases and titles. Returns candidates, and an `ambiguous` flag when it can't pick one. |
| `get_course` | `query` | Resolves "econ", "BU111", etc. to a course. |
| `mark_item_complete` / `mark_item_incomplete` | `item_ref` | Confirmation plus the updated item. |
| `set_item_estimate` | `item_ref, minutes?, difficulty?` | Stored estimate. |
| `create_reminder` | `item_ref?, kind, local_datetime? (ISO, no offset), offset_minutes?, daily_time?, note?` | The absolute fire time in the user's local zone, echoed back. The server rejects past or ambiguous times. |
| `cancel_reminders` | `item_ref? / course?` | Count cancelled. |
| `list_reminders` | — | Active reminders. |
| `update_preferences` | `briefing_enabled?, briefing_time?, evening_alerts_enabled?, default_reminder_offset_min?, weekly_summary_enabled?, preferred_name?` | New preferences. |
| `prioritize` | `horizon_days=3` | A ranked list with reasons and a `missing_info` list (e.g. no estimate for i2). |
| `get_grade_summary` | `course` | Current weighted average, completed vs remaining weight, assumptions. |
| `record_grade` | `course, assessment_label, score, out_of=100` | Matched assessment, new summary. |
| `calculate_required_grade` | `course, target_pct` | Required % on the remaining work, feasibility, assumptions. |
| `search_syllabus` | `course, query` | The top matching chunks and policies, with quotes. |
| `get_item_sources` | `item_ref` | Per-source values and any open conflict. |
| `resolve_conflict` / `set_item_override` | `item_ref, field, value/source` | Recorded user override. |
| `start_syllabus_upload` | `course` | Sets the pending action and returns an upload link. The student can also reply with MMS. |
| `connect_calendar_link` | — | A fresh setup link. |

**Orchestrator rules:** at most 5 tool rounds per message. System prompt: "Only state deadlines, grades, weights, or dates that appear in tool results. If the tools return nothing, say so." Replies are kept under about 480 characters. After the LLM writes a reply, a guard checks that every date or time in it appears in that turn's tool results. If one doesn't, the system regenerates the reply once, then falls back to a templated answer. LLM output is low temperature. Per-user limits: 30 inbound messages/hour and a daily LLM token budget.

**Time parsing:** the LLM turns "at 8" into a local ISO datetime using the current local time it was given. The server then validates it (it must be in the future, and "8" gets the closest-future AM/PM reading) and echoes the absolute time back ("8:00 PM tonight"), so any misreading is visible right away.

## 7. Twilio message flow

1. **Inbound:** check the signature, insert `messages` (where a unique `twilio_sid` gives idempotency), enqueue `handle_inbound`, and return `<Response/>`.
2. **Router (worker), in order:**
   - **Compliance:** STOP/UNSUBSCRIBE/CANCEL set the user to opted out and stop all sends. Twilio Advanced Opt-Out also blocks at the carrier level. START/UNSTOP re-subscribes. HELP sends a canned reply.
   - **Pending action:** a delete-confirmation code, YES/NO to a syllabus confirmation, a conflict choice, or MMS media while a syllabus upload is pending.
   - **Onboarding state machine (no LLM):** `new` → send welcome and consent ("Reply YES to agree to terms: link") → `awaiting_school` (fuzzy match against `institutions.aliases`, e.g. "waterloo", "uw", "laurier", "wlu", "mac"; confirm if unsure) → `awaiting_connection` (send the `/s/{token}` link with the school's LMS name) → the connection succeeds and the initial sync runs → "Connected ✅ I found 5 courses and 14 upcoming items. Next: …" → `active`.
   - **Active:** the AI orchestrator.
3. **Outbound:** every message goes through `outbound_messages` with a `dedupe_key` (e.g. `reply:{inbound_sid}`, `briefing:{user}:{2026-10-05}`, `reminder:{id}:{fire_at}`, `due_offset:{item}:{offset}:{due_at}`). Proactive kinds respect quiet hours: they're held until `quiet_end`, and reminders the user asked for are exempt. The `status` callback updates delivery state. Two failures in a row get logged and the message is marked failed.
4. **Formatting:** a segment-aware formatter. **Any emoji switches the whole message to UCS-2 encoding (70 chars per segment instead of 160), which roughly doubles cost.** Emoji are therefore limited to a small allow-list (✅ 📅 👋), used only in confirmations and alerts, and the formatter counts segments with a cap of about 4 per message.
5. **Number and compliance:** use one Canadian number. Toll-free with Twilio Toll-Free Verification is the fastest compliant A2P route for Canada and the US. A Canadian long code is the alternative, and US students would trigger 10DLC registration. CASL: messages are transactional and opt-in is captured with a version and timestamp. Store `consent_version`.

## 8. Brightspace integration strategy

**One `BrightspaceConnector` family plus per-institution configuration. No per-school code.**

```python
class LMSConnector(Protocol):
    lms_type: str                       # "brightspace"
    method: str                         # "calendar" | "api"
    async def validate(self, conn: ConnectionSecret, inst: InstitutionConfig) -> ValidationResult
    async def fetch(self, conn, inst, since: datetime | None) -> list[RawRecord]

class Normalizer(Protocol):
    def normalize(self, raw: RawRecord, inst: InstitutionConfig) -> NormalizedItem | None

class NormalizedItem(BaseModel):          # source-agnostic domain model
    source: SourceKind; external_source_id: str
    course_code: str | None; course_name: str | None
    title: str; type: ItemType; description: str | None
    available_at / start_at / due_at: datetime | None (UTC); all_day: bool
    points_possible / weight: Decimal | None; cancelled: bool; source_url: str | None
    raw_hash: str
```

**MVP: calendar (iCal) feeds.** The student opens LEARN/MyLearningSpace/etc. → Calendar → Subscribe, then copies the feed URL onto `/s/{token}`. The URL contains a personal token, so it's encrypted with AES-GCM. The server validates it before saving: the host must match `institutions.base_url` or the configured hosts (this blocks SSRF), it has to fetch and parse, and it needs at least one VEVENT. HTTPS only, a 10s timeout, a 5 MB cap, and redirects aren't followed off the allowed host.

**Normalization pipeline (pure functions, covered by fixture tests):**
1. Parse VEVENTs. Convert TZID and floating times to UTC using the institution's timezone. Detect all-day events and treat them as 11:59 PM local only when the institution rules say so.
2. Get the course from LOCATION, DESCRIPTION or CATEGORIES with a default course-code regex (`([A-Z]{2,5})\s?-?(\d{2,4}[A-Z]?)`). Each institution's `config.course_code_regex` can override it.
3. Brightspace emits separate events for one assessment ("… - Due", "… - Availability Ends", "Starts", etc.). Strip those suffixes using the rules in `institution_rules.py` and merge the events into one item with `available_at`, `start_at` and `due_at` filled in.
4. Classify the type with deterministic keyword rules (quiz, midterm, exam, final, lab, discussion, presentation, reading, lecture, assignment), falling back to `other`.
5. Pick the external ID: the VEVENT `UID`, stripped of suffixes, plus the course. If UIDs turn out to be unstable, fall back to a fingerprint `hash(course, normalized_base_title)`. **This is the first thing to check against real feeds.**

**Sync engine:** fetch → normalize → upsert `source_records` by `(user, source, external_id)` → match to an `academic_item` (existing link first, then fuzzy course+title for cross-source matches such as syllabus ↔ calendar) → re-resolve effective values (§9) → diff old against new effective values → write `sync_events` → `change_alerts` batches the significant ones into a single SMS.

**Change detection rules**
- Significant: a new item (only after the first baseline sync), `due_at` moving by 1 minute or more, `start_at` changing for exams, a title change after normalization (case and whitespace ignored), cancellation, or restoration.
- Ignored: description/URL edits, payload hash churn, and items already completed or more than 24h past due.
- **Disappearance:** an item is never cancelled just because it's missing once. Feeds have rolling windows and outages happen. It's marked `possibly_removed` only after it's been missing for 2 or more consecutive successful syncs **and** its due date falls inside the feed's observed window. That earns one gentle message ("BU111 Quiz 3 no longer appears in LEARN. Want me to remove it?").
- A sync that returns 0 events when the last one had N > 5 is treated as a feed error, not a mass deletion.
- Alerts are batched per sync, held during quiet hours, and limited to 1 change-alert SMS per user per hour, merged.

**Sync cadence:** every 2–3h by default, plus a manual "refresh". Syncs are staggered by user to spread load. After 3 failures in a row the student gets one text saying LEARN is disconnected, with a reconnect link.

**Post-MVP / partnership: the Valence API connector.** This needs each institution to register the app (app ID/key or OAuth2 client) and approve scopes. That's how we'd get authoritative grades, submission status, and course enrollments. The connector interface already supports it. It's only enabled for schools where `institutions.connection_methods` includes `api`.

## 9. Source trust and conflicts

Facts are stored per source in `source_records.normalized` and `item_fact_overrides`. The effective value of each field comes from `source_resolver.py`:

1. **An explicit user override** ("prof extended it to Sunday") wins, **unless** a higher-trust LMS source later changes that field. Then a conflict is opened and the student is asked once.
2. `brightspace_api` > `brightspace_calendar` (the live LMS beats static documents).
3. A confirmed `email` (post-MVP) is treated like a user override, since a professor's announcement usually comes before the LMS update. An unconfirmed email opens a conflict and asks.
4. `syllabus` is the lowest priority for dates, because syllabi are written before the term. It's the highest priority for weights and policies.

When two sources disagree by more than 1 hour, the system opens a `conflicts` row, picks a value using the order above, and tells the student once ("I have two dates for BU111 A2 … treating Friday as the deadline. Reply SYL to use the syllabus date."). `get_item_sources` lets the AI explain where any value came from.

**Reconciling completion status:** a status set by the student is never overwritten by a calendar sync. If a later API source reports "submitted", it can set completion. If the API says "not submitted" but the student marked it done, the student's status stands and the discrepancy is only mentioned when asked.

## 10. Syllabus ingestion strategy

**Flow:** "add my BU111 syllabus" → `start_syllabus_upload` sets `pending_action={awaiting_syllabus, course}` and replies "Send a photo or PDF here, or use this link: /u/{token}".
- **The upload link is the main path.** Sending a PDF over MMS from a phone is unreliable across carriers and iMessage fallback. MMS images (photos of the pages) are accepted, and so is a PDF if one does arrive.
- **Inbound media:** download from the Twilio media URL with authentication. Allowed types are PDF, DOCX, JPEG/PNG/HEIC, up to 15 MB. Check the type, store the file in Supabase Storage under `syllabi/{user_id}/{uuid}`, which is private and served through signed URLs only. Then delete the media on Twilio's side.

**Extraction job:**
1. Get the text: `pdfplumber` for PDFs, falling back to a vision LLM for scanned pages. `python-docx` for DOCX. A vision LLM for images.
2. Split into chunks by heading, store them in `syllabus_chunks` with a `tsvector`, and build an FTS index.
3. Run an LLM structured extraction with a strict JSON schema: course code and name, a list of assessments `{name, category, weight_pct, count, drop_lowest, due_date_text, due_at?, is_bonus, confidence 0–1, source_quote}`, policies `{kind, summary, quote, confidence}`, and key dates. **`source_quote` is required, and the server checks that each quote really appears in the extracted text.** If it doesn't, confidence is set to 0.
4. Validate deterministically: do the weights add up to about 100 (bonus excluded)? Does the course code match the target course? Are the dates inside the term? Are there duplicates?
5. Status becomes `needs_confirmation`. Confident items are summarized in one SMS. Uncertain ones are asked about one at a time ("I read the final as 30%, but the weights only add to 95%. Is there a 5% item I missed?"). Nothing goes into `assessment_weights` or creates syllabus-sourced `academic_items` until the student confirms. Explicitly confirmed items are flagged `confirmed=true`.

**Syllabus Q&A:** `search_syllabus` uses structured policies and weights first, then the top-k FTS chunks, and returns quotes. The system prompt requires answers to be grounded in the returned quotes, or to say "your syllabus doesn't say". pgvector embeddings can be added after the MVP if FTS recall turns out weak.

## 11. Grades

`grades/calculator.py` is pure and deterministic, uses `Decimal`, and gets extensive unit tests.
- Inputs: categories `{weight, count, drop_lowest, is_bonus}` and grades mapped to categories.
- `current_average` = the weighted average over graded weight, renormalized. Within a category, items are equally weighted, and the lowest N are dropped only after more than N grades exist.
- `required_on_remaining(target)` = (target − earned contribution) / remaining weight. It reports `feasible` (≤ 100%), `already_secured`, and partial categories.
- It always returns `assumptions[]` (e.g. "assumed the 4 quizzes are equally weighted", "the 5% bonus isn't counted toward the target"), and the reply includes them in short form.
- "I got 72 on the BU111 midterm" → `record_grade` matches "midterm" to a category with fuzzy matching. If it's ambiguous, the bot asks.

## 12. Reminders and scheduling

- **The scheduler tick** (every 30s, using an advisory lock so only one instance runs it) enqueues:
  - `reminders` where `status=active AND fire_at <= now` are claimed with `SKIP LOCKED`. Each one enqueues an outbox message (`dedupe reminder:{id}:{fire_at}`). Then: `absolute` → done; `daily_until_complete` → next day's `fire_at`, or done if the item is complete; `before_due` → done.
  - **Default offset reminders** ("2 hours before everything") aren't materialized as rows. The tick queries items with `due_at - offset <= now < due_at` that are incomplete, with dedupe `due_offset:{item}:{offset}:{due_at}`. When a due date moves, the key changes, so the reminder re-arms on its own.
  - **Briefings:** users whose local time ≥ `briefing_time` and whose `last_briefing_local_date` < local today. The briefing is built from templates plus a short line from the prioritizer, with no LLM needed. It's skipped (or sent as a one-line "nothing due today") according to a preference, and it's never sent at 3 AM after downtime: only within a 2h window.
  - **Evening alerts:** at `evening_alert_time`, items due in the next 6h that are still incomplete.
  - **Weekly summary:** Sunday at 7 PM local time.
  - **Periodic syncs:** connections whose `last_sync_at` is older than the sync interval.
- **When an item's due date changes,** `before_due` reminders get their `fire_at` recomputed. **When an item is completed,** its reminders are cancelled.
- **Anti-spam:** a cap of 6 proactive messages per user per day (reminders the user asked for are exempt), quiet hours, and merging of alerts that fire close together.

## 13. Security and privacy

- **Secrets:** all of them come from environment variables or the platform secret store. iCal URLs and OAuth tokens get AES-256-GCM envelope encryption with versioned keys (`ENCRYPTION_KEYS` lets keys be rotated, and moving to a KMS later is easy). University passwords are never stored or collected.
- **Twilio:** signature validation on every webhook, HTTPS only, and the auth token kept in secrets.
- **Isolation:** every repository method takes `user_id`. Tools get it from the server context. Integration tests check that user A's tools can never see user B's items. RLS is enabled as a second layer.
- **High-risk actions over SMS:** phone numbers can be spoofed or SIM-swapped. Deleting the account, changing the phone number, or replacing the LMS connection needs a confirmation code sent in a separate SMS ("Reply DELETE 4821 within 10 min"), or the one-time link. Reads and marking items complete are low risk and allowed.
- **Data minimization:** message bodies are kept for 90 days and app logs never contain them (PII is redacted). Only academic data is stored, plus explicit preferences.
- **Account deletion:** "delete my account" → a confirmation code → everything is hard-deleted (storage files, records, connections), the deletion is logged in `audit_log` without PII, and a final confirmation text goes out.
- **Audit logging:** connections, deletions, preference changes and admin access.
- **Compliance:** PIPEDA (and Ontario FIPPA for any later university partnerships). Data stays in Canada. The consent text links to the privacy policy. CASL opt-in and opt-out are recorded.
- **Uploads:** size and type limits. Files are parsed only in the worker, never executed. Storage paths are private.

## 14. Scope split

| MVP | Post-MVP | Needs a university partnership |
|---|---|---|
| Phone signup, consent, school selection | Weekly "spread your work" planning | Valence API/OAuth connector |
| iCal ingestion, normalization, course detection | Email connector (as a source) | Authoritative submission status → completion |
| Today/week/next/exams/workload queries | pgvector syllabus search | Official grade sync |
| Briefings, reminders, evening alerts | Admin console (sync failures, connector health, delivery errors) | Roster-level onboarding, school-branded number |
| Completion tracking | Prioritization with study availability | Canvas/Blackboard/Moodle connectors (just a new `connectors/<lms>/` package) |
| Change detection and alerts | Billing | |
| Syllabus upload, extraction, confirmation, Q&A | Multiple schools per user | |
| Grade entry and calculator, estimates, basic prioritization | | |
| Source conflicts (calendar vs syllabus vs user) | | |

## 15. Development phases (exact order)

0. **Viability spike (about 1 week, throwaway-quality code under `spikes/`):** collect real Brightspace iCal feeds from 3–4 schools (Waterloo, Laurier, McMaster, Guelph, using our own or friendly students' accounts). Find out: whether subscription is enabled on each instance, UID stability across two fetches days apart, course naming in events, Due/Availability event patterns, timezone handling, and the feed window. Also send and receive one SMS through a Canadian Twilio number. **Stop if:** most target schools have calendar subscription turned off. If so, syllabus-first becomes the main ingestion path.
1. **Foundation:** pyproject, config, Docker Compose Postgres, Alembic baseline, models for users, institutions and preferences, institution seed YAML, pytest plus CI, health endpoints.
2. **SMS loop:** Twilio webhook with signature checks, the messages table, the Postgres job queue and worker, the outbox sender, status callbacks, compliance keywords, an echo bot.
3. **Onboarding state machine:** consent, school fuzzy match, setup tokens, the `/s/{token}` page.
4. **The calendar connector and normalizer, turned into production code with ICS fixture tests from the spike,** plus the sync engine (source_records, matcher, upsert) and the "Connected ✅ found N courses" reply.
5. **Queries:** item repositories and tools (`list_items`, `next_items`, `get_exams`, `find_items`, `get_workload`), the LLM orchestrator with the date-grounding guard, and conversation state.
6. **Completion and estimates:** the mark complete/incomplete and estimate tools.
7. **Scheduler:** the tick loop, periodic sync, reminders (all three kinds plus default offset), briefings, evening alerts, quiet hours, the daily cap.
8. **Change detection:** the diff rules, sync_events, batched alerts, disappearance handling.
9. **Syllabus:** upload page plus MMS intake, storage, text extraction, LLM extraction with quote checks, validation, SMS confirmation, FTS Q&A.
10. **Grades:** calculator (built test-first), the record_grade, summary and required-grade tools.
11. **Source resolver and conflicts,** plus prioritization and the weekly summary.
12. **Hardening:** deletion flow, high-risk confirmations, rate limits and LLM budget, RLS, audit log, structured logging and error tracking (Sentry), load test of the scheduler, deploy to Railway or Fly (web plus worker), and a closed beta with about 20 students.

## 16. What proves technical viability first

1. **The iCal spike (Phase 0 plus 4):** can a calendar feed from multiple Ontario Brightspace instances be turned reliably into clean items with stable IDs, matched to courses? This is the biggest risk, and it decides whether the whole approach works without partnerships.
2. **An end-to-end SMS round trip (Phases 2, 3 and 5):** text "what's due this week" from a real phone and get a correct, grounded answer in under 10s.
3. **Change detection on a real feed (Phase 8):** edit a due date in a test course, or watch a real one move, and get exactly one correct alert.
4. **Syllabus extraction accuracy:** run 20 real Ontario syllabi through the extractor and measure weight-extraction accuracy and confirmation rate.

## Key technical risks

- **Calendar subscription may be disabled, or behave differently, per institution.** Mitigation: Phase 0 survey, `institutions.connection_methods` flags, and syllabus-only mode as a fallback.
- **Feed URL tokens can be revoked or rotated** when the student resets them. Mitigation: failure detection plus an SMS reconnect link.
- **iCal feeds have no submission status and no grades.** Mitigation: local completion tracking and student-entered grades. The API arrives later.
- **Feed window limits** (feeds may only cover a date range). Mitigation: the disappearance rules above.
- **A2P registration and carrier filtering delays.** Mitigation: start toll-free verification in week 1.
- **LLM hallucination.** Mitigation: tool-only facts, the date-grounding guard, templated briefings, low temperature.
- **SMS cost from UCS-2 emoji and long replies.** Mitigation: the segment-aware formatter and caps.
- **Mixing up phone-number identity.** Mitigation: confirmation codes for high-risk actions.

---

## Verification (how each phase is checked)

- **Unit tests (pytest):** the normalizer against real anonymized ICS fixtures from each school; the change detector (moved, renamed, cancelled, disappeared, empty-feed guard); the grade calculator (drops, bonus, partial categories, infeasible targets); time parsing (AM/PM inference, timezones, DST edges on Nov 1 2026); the source resolver priority rules.
- **Integration tests** against Docker Postgres: the sync engine is idempotent (syncing twice creates no duplicates and no events); user isolation (A can't read B through any tool); outbox dedupe (a retried send sends nothing new); scheduler claims under concurrency (two workers produce no double sends).
- **Conversation evals:** a scripted suite of about 50 SMS prompts from the spec ("what's due today", "remind me about cs at 8", "I finished econ", "what do I need on the final for an 80") run against seeded data, checking which tools were called and that no dates appear in a reply unless a tool returned them.
- **End-to-end:** a real Twilio number plus ngrok in dev. A real phone goes through onboarding, connects a real Brightspace feed, asks questions, sets a reminder 2 minutes out and receives it, uploads a syllabus and confirms it.
