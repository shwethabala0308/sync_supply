# SyncSupply

Multilingual, voice-enabled web app that suggests traditional Ayurvedic home
remedies from a user's symptoms — built per the Developer Handoff Guide
(Oct 2026), Phase 1 (MVP) scope.

> **Disclaimer:** SyncSupply gives traditional home-remedy information. It is
> not medical advice and does not replace a doctor. Every remedy shown is
> filtered through a safety layer, but the dataset is seed content and must
> be reviewed by a qualified Ayurvedic practitioner before any real-world use.

## What's implemented (Phase 1 / MVP)

- 7-day free trial, no login required, identified by an anonymous device ID
- Email/phone + password signup & login after the trial, which carries the
  trial user's profile and history forward
- Profile (name, age, weight, DOB) and health details (allergies, pregnancy,
  blood thinners, thyroid medication, diabetes) collected before symptoms
- Symptom input by **text and voice** (Web Speech API) in all 7 languages —
  English, Hindi, Tamil, Kannada, Malayalam, Telugu, Marathi
- **Dynamic** weighted keyword matching (section 7 of the guide) driven
  entirely by the dataset in the database — no herb, condition or remedy is
  hard-coded in application code; edit the CSVs and the app's behavior changes
- Full safety filter: allergy hiding, honey-for-infants rule, avoid-if vs.
  profile flags (pregnancy / blood thinners / thyroid / diabetes / age),
  red-flag (emergency) detection that skips remedies entirely
- Nearby Ayurvedic shops/hospitals via Google Places, with graceful
  fallback when no API key is configured and a cache to control free-tier
  Maps cost
- Language switcher, large-text mode, PWA (installable, offline shell)
- REST API with the exact `/api/symptoms/match` contract from the guide

**Not yet built** (explicitly out of scope / later phases per the guide):
AI/NLP semantic matching (Phase 2), Odia/Gujarati (Phase 3), online herb
ordering (Phase 4), diagnosing disease, or prescriptions.

## Architecture

```
┌──────────────┐      HTTPS/JSON      ┌───────────────────┐      SQL      ┌────────────┐
│ React PWA    │ ───────────────────► │ FastAPI backend    │ ─────────────► │ PostgreSQL │
│ (Vite)       │ ◄─────────────────── │ matching + safety  │ ◄───────────── │            │
└──────────────┘                      └───────────────────┘                └────────────┘
       │                                       │
       │ Firebase/JWT auth (simplified: JWT)   │ Google Places API
       ▼                                       ▼
  Web Speech API                        Nearby hospitals/shops
```

- **Frontend:** React 18 + Vite, installable as a PWA, `react-i18next` for
  the 7 UI languages, Web Speech API for voice input and read-aloud.
- **Backend:** FastAPI + SQLAlchemy, JWT auth (device-ID trial or
  email/phone login), Pydantic-validated REST API.
- **Database:** PostgreSQL in production (SQLite with zero setup for local
  dev — same code, just change `DATABASE_URL`).
- **Dataset:** `backend/data/*.csv` are loaded into the database once at
  startup (`app/data_loader.py`); nothing is pre-defined in Python.

## Repository layout

```
syncsupply/
├── backend/            FastAPI app, CSV→DB loader, matching & safety engine
│   ├── app/
│   ├── data/            herbs.csv, conditions.csv, remedies.csv
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
├── frontend/           React PWA (Vite)
│   ├── src/
│   │   ├── pages/        Splash, Login, Profile/Health setup, Symptom, Results, Emergency, Nearby
│   │   ├── locales/       en/hi/ta/kn/ml/te/mr.json
│   │   ├── context/       AppContext (auth, profile, language, large-text)
│   │   └── services/api.js
│   ├── Dockerfile
│   └── .env.example
├── docker-compose.yml   One command local stack (Postgres + API + web)
├── render.yaml          Render free-tier blueprint (API + free Postgres)
├── .github/workflows/ci.yml
└── README.md
```

## Run locally — fastest path (SQLite, no Docker)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # SQLite by default, works out of the box
uvicorn app.main:app --reload

# Frontend (new terminal)
cd frontend
npm install
cp .env.example .env                                  # points at http://localhost:8000
npm run dev
```

Open http://localhost:5173. The dataset loads into `backend/syncsupply.db`
automatically on first startup.

## Run locally with Docker (Postgres, matches production)

```bash
docker compose up --build
```

- Frontend: http://localhost:8080
- Backend: http://localhost:8000 (`/api/health` for a liveness check)
- Postgres: localhost:5432 (user/pass/db: `syncsupply`)

## Free-tier deployment

**Backend + database — Render**
1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at the repo. `render.yaml` at
   the repo root provisions a free PostgreSQL database and a free Docker web
   service for `backend/`, wires `DATABASE_URL` automatically, and generates
   `JWT_SECRET`.
3. Set `CORS_ORIGINS` to your deployed frontend URL and (optionally)
   `GOOGLE_MAPS_API_KEY` in the Render dashboard.
4. Render builds `backend/Dockerfile` and runs `uvicorn` on the `$PORT` it
   assigns — no code changes needed.

*(Railway, Fly.io or Supabase+any Docker host work the same way — only
`DATABASE_URL` and `CORS_ORIGINS` need to change.)*

**Frontend — Vercel (or Netlify)**
1. Import the repo in Vercel, set the project root to `frontend/`.
2. Add an environment variable `VITE_API_BASE_URL` = your Render backend URL.
3. Vercel picks up `frontend/vercel.json` automatically (SPA rewrites for
   client-side routing). Deploy — Vercel's free tier covers a PWA like this.

Both free tiers put the backend to sleep when idle; the first request after
idle may take a few seconds while it wakes up. This is a known free-tier
trade-off, not an app bug.

## Environment variables

See `backend/.env.example` and `frontend/.env.example` for the full,
commented list. The only one you must set for Maps to work is
`GOOGLE_MAPS_API_KEY` (restrict it by HTTP referrer/domain in the Google
Cloud console); without it, the nearby-care screen degrades gracefully
instead of erroring.

## API

The backend follows the contract in section 11 of the handoff guide,
including the error shape `{ "error": { "code": "...", "message": "..." } }`.
Interactive docs are available at `/docs` (Swagger UI) once the backend is
running. Key endpoints:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/trial` | Start/resume the anonymous 7-day trial |
| POST | `/api/auth/signup` / `/api/auth/login` | Account auth |
| GET/PUT | `/api/profile`, `/api/profile/health` | Profile & health/allergy flags |
| POST | `/api/symptoms/match` | Core matching endpoint (text or voice transcript) |
| GET | `/api/symptoms/history` | Past queries |
| POST | `/api/places/nearby` | Nearby Ayurvedic/hospital/homeopathy places |
| GET | `/api/reference/herbs`, `/api/reference/conditions` | Dataset lookups used by the UI |

## Schema migrations vs. dataset updates

These are two different things:

- **Dataset content** (herbs/conditions/remedies) changes by editing the
  CSVs — `app/data_loader.py` upserts them automatically on restart (see
  below). No migration needed.
- **Database schema** changes (a new column, a new table) need an Alembic
  migration against a real Postgres database:
  ```bash
  cd backend
  alembic revision --autogenerate -m "describe the change"
  alembic upgrade head
  ```
  `Base.metadata.create_all()` (used at startup) only creates tables that
  don't exist yet — it never alters or drops anything, so it's fine for a
  fresh local SQLite file but is not a substitute for migrations against a
  production database with existing data.

## Updating the dataset

Edit `backend/data/{herbs,conditions,remedies}.csv` (keep the BOM/UTF-8
encoding and the `|`-separated keyword format) and restart the backend, or
call `app.data_loader.load_dataset()` to force a reload — it upserts rows,
so existing user data is untouched. Per the handoff guide, have a qualified
Ayurvedic practitioner review every remedy and record the reviewer/date
(the `dataset_versions` table has fields for exactly this) before treating
any update as launch-ready.

## Remediation status (against the Oct 2026 audit)

An external audit of the first build flagged nine findings and a required-
outcome checklist. Status after this pass, stated plainly rather than
rounded up:

| Finding | Status |
|---|---|
| Auth didn't match the specified OTP flow | **Fixed.** `/api/auth/otp/request` + `/api/auth/otp/verify` replace password login; pluggable SMTP/Twilio delivery, console logging for free-tier dev. |
| Trial bypassable via device-ID reset | **Mitigated, not solved.** New trial starts are now rate-limited per IP (5/day by default); a determined user on a fresh IP can still bypass. Full device attestation is out of scope for a browser PWA. |
| Safety filter was fuzzy-text-only | **Hardened.** Remedies now carry structured `avoid_tags` (pregnancy/diabetes/blood_thinners/thyroid/infant/elderly/...), checked first; text matching is now a fallback, not the only layer. Tags were generated heuristically from the existing English text — still need practitioner sign-off. |
| Emergency phrase coverage incomplete | **Expanded, inherently incomplete.** ~3x more phrases per language (seizures, stroke signs, poisoning, anaphylaxis, etc.). A test (`test_emergency_known_gap_documented`) deliberately shows a misspelling slipping through — substring matching cannot be made exhaustive; Phase 2's semantic matching is the real fix. |
| Symptom text stored without consent | **Fixed.** `consent_store_history` defaults to **False**; matching works either way, but raw text is only persisted after explicit opt-in. Added history deletion, full JSON export, and account deletion endpoints. |
| Dataset loader lacked validation/transactions | **Fixed.** Loader now validates required columns, duplicate IDs, and referential integrity before writing, runs inside a transaction with rollback on failure, and skips reloading unchanged data via a content hash. |
| Nearby-care missing distance/phone | **Fixed.** Haversine distance now computed from the user's coordinates; phone numbers fetched via Place Details for the top 5 results (capped to protect free-tier quota); hospital searches use a tighter, distance-sorted radius. |
| No dedicated test suite | **Fixed.** `backend/tests/` — 80+ tests covering matching (10 EN + 10 HI sentences, smaller samples for TA/KN/ML/TE/MR), safety negative cases (allergy, pregnancy, diabetes, blood thinners, honey-for-infants, a "nothing hidden ever leaks into visible" invariant), auth/OTP, consent-gated history, dataset validation, and rate limiting. CI now runs `pytest -q`, not just an import check. |
| No monitoring/backup/migration story | **Not done.** Free-tier Render/Railway give basic uptime dashboards and Postgres backups out of the box, but no alerting, restore drill, or migration tooling (e.g. Alembic) was added — the loader's upsert behavior covers simple dataset updates, not schema changes. |

### Required-outcome checklist, re-assessed

| Outcome | Status | Notes |
|---|---|---|
| Splash + 7-day trial | Improved | IP rate-limiting added; still no hard device binding |
| Authentication | **Fixed** | OTP flow now matches spec |
| Profile/health details | Partial | Forms + validation exist; still no automated frontend tests |
| 7-language UI | Partial | All locale files complete; still needs native-speaker review |
| Voice input/output | Partial | Implemented via Web Speech API; not verified on real Android/iOS devices |
| Dynamic keyword matching | **Validated** | Now covered by parametrized tests across all 7 languages |
| Allergy/contraindication filtering | Improved | Structured tags + invariant test; tags need clinical review |
| Red-flag emergency handling | Improved | Coverage expanded; exhaustiveness explicitly not claimed |
| Remedy instructions/warnings | Unchanged | Still English-only; translation is a content task, not code |
| Nearby Ayurvedic care | Improved | Distance + phone + hospital-first radius added |
| Emergency hospital referrals | Improved | Same as above, verified via haversine distance sort |
| Disclaimer | Unchanged | Shown on every result screen |
| PWA/offline | Unchanged | Configured, not tested offline on a real device |
| Performance/accessibility targets | **Not measured** | No load-time, touch-target, or contrast audit was run |
| Privacy/security | **Substantially improved** | Consent gating, export, deletion, rate limiting, and transactional dataset loading all added this pass |
| Automated/E2E testing | **Backend done, frontend not** | No Playwright/Cypress suite for the 7 language UI flows yet |
| Deployment | Configured, re-verified | Backend smoke-tested end-to-end after every change in this pass; still not deployed to live Render/Vercel URLs |

## Second remediation pass (P0 items from the follow-up audit)

A second, more detailed audit (A–G below) went deeper on medical safety,
auth/privacy/backend security, and testing. Status after this pass:

| Area | Item | Status |
|---|---|---|
| A. Medical safety | Structured contraindications | **Done** (prior pass) — `avoid_tags` |
| A | Allergy safety / normalized identifiers | Improved — herb IDs are now schema-validated and normalized to uppercase on input |
| A | Medicine interaction dataset | **Not done** — no professionally-reviewed drug-interaction dataset exists to integrate; flagged, not fabricated |
| A | Red-flag detection | Improved (prior pass); still not clinically reviewed |
| A | **Fail-safe behavior** | **Done** — if safety evaluation raises for a condition, that condition's remedies are withheld entirely (counted as hidden), never shown unfiltered |
| A | Clinical content governance | **Done** — `reviewed_by`/`reviewed_at`/`evidence_note` columns added to the remedy model and CSV; `/api/reference/review-status` reports the real count honestly (currently **0 of 28 reviewed** — nothing fakes a sign-off) |
| A | Follow-up guidance | Unchanged — `see_doctor_if` shown on every remedy |
| B. Auth/privacy/security | Authentication completeness | OTP expiry, attempt limits, and cooldown were already in place; account recovery (lost access to email/phone) is still **not implemented** |
| B | **Authorization** | **Fixed a real gap**: `get_current_user` previously auto-created a trial user from *any* `X-Device-Id` header, bypassing `/trial`'s rate limit entirely. It now only *resolves* existing identities (JWT or trial cookie) and never silently creates one. Verified live and covered by a regression test. |
| B | **Trial enforcement** | **Substantially improved** — trial identity is now anchored to a server-generated token in an HttpOnly, non-client-readable cookie, not a client-supplied device ID. Verified live. Residual: clearing cookies + a fresh IP still resets a trial (inherent to any anonymous-trial design); IP rate-limiting remains the backstop. |
| B | Rate limiting | Extended — nearby-care (external Google Maps calls) now has its own stricter limit (15/min) separate from general API limits |
| B | Input validation | **Done** — allergy herb IDs and language codes are now schema-validated (422 on malformed input) instead of silently passed through |
| B | Secrets management | **Done** — the app refuses to start in `ENV=production` with a default/empty `JWT_SECRET`; Google Maps key and DB credentials were already env-only, never in the frontend bundle |
| B | Privacy controls | Unchanged from prior pass (consent, export, deletion) + **retention limits added**: expired OTP codes and stale Places cache rows are purged on startup |
| B | **Audit logging** | **Done** — a narrow `audit_logs` table records trial starts, OTP request/verify outcomes, consent changes, and account deletions, with a test proving raw symptom text never appears in it |
| B | Database security | Partial — least-privilege DB user and backup/restore drills are a hosting-config task (documented below), not code; **Alembic migration scaffolding added** (`backend/migrations/`) with a generated baseline migration, as the schema-change path distinct from the CSV data loader |
| C. Multilingual/voice | All items | **Not done this pass** — translation review, per-language speech testing, Indic font/contrast verification, and real low-cost-Android/Safari testing all require either a qualified translator or physical devices this environment doesn't have. Added: mic-permission-denial now shows the same manual-entry fallback as an unsupported browser; `<html lang>` now syncs with the active language for screen readers. |
| D. AI/NLP matching | All items | **Not done** — explicitly Phase 2 in the original guide; the current matcher is the specified Phase 1 weighted-keyword algorithm, not semantic embeddings. |
| E. Nearby care | Distance/sorting, phone, separate search strategies | **Done** (prior pass) | 
| E | Emergency independent of map search | **Confirmed by design** — the Emergency screen renders the `112` call button from the match response alone; it never waits on or depends on a Places API call |
| F. Frontend/accessibility | Blank-screen prevention | **Done** — added a React error boundary so a render error shows a recovery screen instead of white space |
| F | Accessible components | Partial — added `aria-live`/`role="alert"` on the emergency screen and voice-status region; a full keyboard-navigation and contrast audit was not performed |
| F | Session handling | Unchanged — JWT expires after 7 days; no explicit "idle logout" |
| G. Testing/CI | Expanded suite | **Done** — 96 tests now (was 82), covering authorization isolation between two separate trial identities, audit-log content, input validation, retention cleanup, and the cookie-identity mechanism; CI runs `pytest -q` |
| G | Security tests | Added cross-user isolation and "no silent user creation" regression tests; did not add injection/fuzzing tests |
| G | Performance/recovery tests | **Not done** — no load, concurrency, or restore-drill tests were added |

### What a genuine production launch still needs that no amount of code can substitute for
- A qualified Ayurvedic practitioner reviewing every remedy, allergy tag, and contraindication, then populating `reviewed_by`/`reviewed_at` — today that's honestly reported as 0/28.
- Native speakers reviewing the 7 UI translations and the (still English-only) remedy instructions.
- Real-device testing: low-cost Android phones and Safari/iOS, specifically for voice recognition and PWA install/offline behavior.
- A decision on account recovery (OTP-only auth has no password to reset, but losing access to the registered email/phone currently has no recovery path).
- Production database operations: a least-privilege DB role (not the default Render user), a tested backup-restore drill, and load/concurrency testing before real traffic.

## Third remediation pass (P0 items from the second follow-up audit)

This audit raised the bar further, with explicit acceptance criteria and a
clear statement that passing tests ≠ medical safety. Status below is split
into **engineering work done and verified** vs. **requirements that need a
qualified human, real infrastructure, or legal counsel — which this pass
does not fabricate evidence for.**

### A. Medical safety — engineering status

| Gap | Status |
|---|---|
| Incomplete contraindication rules | Unchanged from prior passes — structured `avoid_tags` cover pregnancy/diabetes/blood-thinners/thyroid/age; medicine-interaction data still doesn't exist to integrate |
| English-only warning matching | Unchanged — tags are language-independent (a tag like `pregnancy` applies the same regardless of UI language), so this is actually already addressed structurally, just not stated clearly before |
| Incomplete red-flag coverage | Unchanged — expanded phrase lists per prior passes; **not** qualified-reviewer-authored |
| Unverified remedy content | Unchanged — `/api/reference/review-status` still honestly reports 0/28 reviewed |
| Uncertain matches → abstain | **Already true by construction** — `MIN_MATCH_SCORE` and the no-match message mean low-confidence input never silently returns a remedy; added no new code here because the behavior already existed |
| **Safety-service failures → fail closed** | **Done, now with a dedicated test** — if safety evaluation raises for a condition, that condition's remedies are withheld entirely (previous pass implemented this; this pass added explicit test coverage) |
| Disclaimer placement | **Fixed** — was missing from the onboarding (profile/health) and Emergency screens; now present on every screen the guide requires |

**What this pass will not pretend to satisfy:** "every defined safety scenario has a passing test" and "every published remedy has a verifiable reviewer" require a qualified Ayurvedic practitioner's actual review work — not something achievable by writing more code. The honest state is tracked by `/api/reference/review-status`, not asserted in prose.

### B. Authentication, authorization, trial security — engineering status

| Gap | Status |
|---|---|
| **OTP replay** | **Fixed** — consumption is now a single atomic `UPDATE ... WHERE consumed_at IS NULL`; a reused code is rejected. Verified live against a running server. |
| **Concurrent OTP verification** | **Fixed** — the same atomic UPDATE means two concurrent requests with a correct code cannot both succeed; the loser gets rowcount 0 and a clean error, not a race. |
| OTP abuse (expiry/attempts/cooldown/rate limits) | Unchanged from prior pass — already covered (10-min expiry, 5 max attempts, 60s cooldown, per-IP rate limiting) |
| OTP delivery failure | Unchanged — a provider failure raises a clear `otp_delivery_unavailable` error rather than pretending a code was sent |
| **Session lifecycle** | **Fixed a real gap**: JWTs were stateless — "logout" only cleared client storage, so a copied/stolen token kept working for up to 7 days regardless. Added server-side revocation: tokens carry a `jti`, logout and account deletion revoke it via a denylist table, and `get_current_user` checks it. Verified live: the same token that worked before `/logout` gets `401 session_revoked` immediately after. |
| Cross-user access | Unchanged from prior pass — every profile/health/history/account endpoint already scopes strictly to `get_current_user()`'s row; covered by the two-separate-trial-identity isolation test |
| Trial bypass | Unchanged — documented, not claimed solved (see prior passes' notes on the cookie-based trial anchor) |
| **Account linking** | **Fixed a real vulnerability**, not a hypothetical one: OTP verify previously could silently overwrite an *already-verified* account's email/phone if a stale/reused `device_id` happened to match it — effectively an account-takeover-adjacent bug. Now a device-linked row is only adopted if it's genuinely still anonymous (no email, no phone, not verified). Caught by a regression test simulating two different people sharing one device_id. |

### C. Privacy and health-data protection

| Gap | Status |
|---|---|
| Consent enforcement | Unchanged from prior pass — `consent_store_history` is checked server-side before any symptom text is persisted, not just a UI toggle |
| Data minimization | Unchanged — matching happens synchronously without requiring storage; text is only kept on explicit opt-in |
| Data export / deletion | Unchanged — `/api/profile/export` and `/api/profile/account` (DELETE) |
| **Retention enforcement** | Extended this pass: expired revoked-token rows are now also purged on startup (alongside OTP codes and stale Places cache from the prior pass). **Not done:** these run as a startup hook, not a monitored scheduled job — "ensure their failure is visible" needs real alerting infrastructure (see P2 below). |
| Audit trail | Unchanged — the existing audit log now also records `logout` and `otp_replay_blocked` events; still no health data in it, verified by an existing test |
| Encryption | **Not verified in this environment** — HTTPS is enforced by the hosting platform (Render terminates TLS), not application code; encryption-at-rest is a property of the managed Postgres provider. Nothing to "fix" in this repo, but also nothing I can test from here. |
| Access controls / Compliance | **Explicitly not addressed** — restricting production access to authorized personnel is an organizational/hosting-account control, and a DPDP Act (India) compliance review needs actual legal counsel. I'm not going to write prose that implies either is "done." |

### D. Database integrity and migrations

| Gap | Status |
|---|---|
| **create_all() vs. Alembic inconsistency** | **Fixed — this was a real, correctly-flagged bug.** Startup now branches on the database type: SQLite (local dev) still uses `create_all()` for zero-setup convenience; any non-SQLite `DATABASE_URL` (i.e., every real deployment) **refuses to start** if the schema isn't already initialized, and requires `alembic upgrade head` first. |
| **Migration/startup race** | **Fixed.** Previously both the Dockerfile's `CMD` and `render.yaml`'s `preDeployCommand` ran `alembic upgrade head`, which could race on a rolling deploy. Now `preDeployCommand` is the *only* place migrations run (Render runs it to completion, once, before any instance starts); the container entrypoint instead runs `app/verify_schema.py`, which fails fast if the live DB isn't already at the code's expected revision — see `backend/app/verify_schema.py`. |
| **Console OTP provider could leak real codes in production** | **Fixed.** `OTP_PROVIDER=console` is now refused outright at startup when `ENV=production` (and `send_code` refuses again as a second guard even if that check is ever bypassed). [Resend](https://resend.com) is wired up as a genuinely free (no-card) email OTP provider — set `OTP_PROVIDER=resend` and `RESEND_API_KEY`. SMTP and Twilio remain available for your own provider. |
| **OTP delivery failures weren't verified** | **Fixed.** SMTP, Resend and Twilio calls now check the actual send result (SMTP refusal list, HTTP status, and for Twilio the message `status` field) and raise `OtpDeliveryError` on any failure — the auth router deletes the just-created OTP record on delivery failure rather than leaving a code the user never received sitting in the database as usable. |
| **In-memory rate limiting doesn't scale across instances/workers** | **Fixed (opt-in).** `app/rate_limit.py` now accepts `REDIS_URL` and shares counters across every process via Redis when it's set (e.g. a free [Upstash](https://upstash.com) Redis database) — logging a loud warning at production startup if it's unset, since a single free-tier instance/worker is still fine without it. The `X-Forwarded-For`-based client IP is now read from the right (trusting exactly `TRUSTED_PROXY_HOPS`, default 1, hops added by a real proxy in front of the app) instead of the left, which previously let a client forge its own apparent IP and dodge rate limits by sending its own `X-Forwarded-For` header. |
| Fresh init + upgrade tested | Baseline migration regenerated and its `op.create_table`/`ForeignKeyConstraint` calls inspected directly; **not** tested against a real Postgres instance (none available here) |
| **FK constraints, unique constraints, indexes** | Added `ondelete="CASCADE"` to every user/condition/herb foreign key (previously present but without cascade behavior); added `PRAGMA foreign_keys=ON` for SQLite dev so cascade behavior isn't silently Postgres-only |
| Atomic/idempotent/validated dataset imports | Unchanged from an earlier pass — transaction + validation + content-hash skip were already in place |
| Rollback/forward-recovery for failed migrations | **Not tested** — no real failed-migration scenario was exercised |
| **Connection pooling/timeouts** | Added this pass — explicit `pool_size`, `max_overflow`, `pool_recycle`, and `pool_timeout` for non-SQLite connections (previously using SQLAlchemy's bare defaults) |
| Least-privilege DB credentials | **Not done** — this is a database-user-creation step on whatever Postgres host you use, documented but not executable from here |

### E. Run and validate the actual test suite

Run, not just written, in this environment:

```
101 passed, 1 skipped in 0.99s
```

(1 skip is `test_rate_limit.py`'s isolated-limiter test, which deliberately skips when the suite-wide high rate-limit override is active — see that file's own comment.) Also verified live against a running `uvicorn` instance for everything a pure unit test can't prove: cookie `Set-Cookie` headers, actual rate-limit HTTP responses, OTP replay over real HTTP round-trips, and token revocation. What I can also honestly confirm: tests run against an isolated SQLite file (never production data), the OTP tests use the `console` provider (never a real paid SMS/email call), and CI (`.github/workflows/ci.yml`) runs `pytest -q`, not just an import check. What I cannot confirm from this environment: a "clean environment" CI run on GitHub's actual runners (I've only run it in this sandbox), since I have no way to trigger or observe an Actions run from here.

### P1 (seven-language completeness, voice, nearby care, accessibility/offline) and P2 (monitoring, alerting, backups, load testing, security scanning, release safety)

**Not addressed in this pass**, and I want to be direct about why: these require things this environment genuinely does not have — native-speaker translators, physical Android/iOS devices, a live production deployment to monitor, a load-testing target, and a dependency-vulnerability scanner with a real report to review. Writing code that *claims* to satisfy "tested alert delivery" or "successful restore drill" without actually performing them would be fabricating evidence, which the audit itself explicitly warns against ("I would not invent a coverage percentage as a substitute for meaningful tests"). The honest position: the P1 items are mostly still at the same "implemented, not device-tested" status as prior passes, and every P2 item remains genuinely undone.

## Open questions carried over from the handoff guide

These are product/ops decisions, not engineering gaps — see section 14 of
the original guide: who reviews the dataset as a qualified practitioner,
who translates/reviews the non-English preparation and warning text (today
shown in English per the guide's "known gap" note), the Maps/speech budget
owner, and the target date for Odia/Gujarati.
