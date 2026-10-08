# Bulk Certificate Generator

Backend API that accepts **one request with many recipients**, generates a PDF certificate
for each valid recipient from a single predefined template, tracks progress, and lets the
client download the results (one by one, or all as a ZIP).

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default), ReportLab (PDF), pytest.

---

## 1. Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
```

## 2. Run

```bash
uvicorn app.main:create_app --factory --port 8000
```

- Interactive API docs: http://localhost:8000/docs
- Data is stored in `./data/` (SQLite DB + generated PDFs). Delete the folder to reset.
- Run **a single worker process** (see "Design decisions" for why).

Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL; install its driver) |
| `STORAGE_DIR` | `./data/certificates` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Upper limit per request |
| `RESUME_ON_STARTUP` | `true` | Resume interrupted jobs when the server starts |

## 3. Run the tests

```bash
pytest
```

Tests use a temporary SQLite DB and temp directory per test; nothing touches `./data`.
They cover job creation, validation, certificate generation, job status/progress,
single-certificate failure, retrieval, and crash recovery.

## 4. Submit a certificate generation request

`POST /jobs` returns **202 Accepted** immediately; certificates are generated in the background.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Content-Type: application/json" \
  -d @sample_request.json
```

Body:

```json
{
  "event_name": "Python Backend Bootcamp 2026",
  "issuer": "Example Academy",
  "issue_date": "2026-10-08",
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "Rahul Sen"}
  ]
}
```

| Field | Required | Rules |
|---|---|---|
| `event_name` | yes | 1-200 chars |
| `issuer` | no | default `"Certificate Authority"` |
| `issue_date` | no | `YYYY-MM-DD`, default today (UTC) |
| `recipients` | yes | 1 to `MAX_RECIPIENTS_PER_JOB` items |
| `recipients[].name` | yes | 1-100 chars, no control characters, Latin characters only (see limitations) |
| `recipients[].email` | no | valid email if given |

Response (`202`, with a `Location: /jobs/{id}` header):

```json
{"id": "826006d1-...", "status": "pending", "total": 5, "succeeded": 0, "failed": 2,
 "pending": 3, "progress_percent": 40.0, "...": "..."}
```

### Check progress

```bash
curl http://localhost:8000/jobs/<job_id>
```

`status` is one of `pending` -> `processing` -> `completed` (all succeeded) /
`completed_with_errors` (some failed) / `failed` (none succeeded).
`succeeded`, `failed`, `pending` and `progress_percent` update after every certificate.

### See which certificates succeeded or failed

```bash
curl "http://localhost:8000/jobs/<job_id>/certificates"                    # all
curl "http://localhost:8000/jobs/<job_id>/certificates?status=failed"      # only failures
```

Each item has `index` (0-based position in your request), `status`
(`pending|succeeded|failed`), `error` (reason when failed) and `download_url`.
Supports `limit` / `offset`.

## 5. Retrieve generated certificates

```bash
# one certificate (use download_url from the listing)
curl -o cert.pdf http://localhost:8000/certificates/<certificate_id>/download

# every successful certificate of a finished job, as a ZIP
curl -o certificates.zip http://localhost:8000/jobs/<job_id>/download
```

| Endpoint | Notes |
|---|---|
| `POST /jobs` | create job (202) |
| `GET /jobs`, `GET /jobs/{id}` | list jobs / job status |
| `GET /jobs/{id}/certificates` | per-recipient results, filter by `status` |
| `GET /jobs/{id}/download` | ZIP; `409` while job unfinished, `404` if nothing was generated |
| `GET /certificates/{id}/download` | PDF; `409` if that certificate failed/pending |
| `GET /health` | liveness |

---

## 6. Important implementation / design decisions

**Asynchronous (background) processing.** A job can hold up to 1000 recipients; generating
them inside the HTTP request would risk client/proxy timeouts and tie up a worker. So
`POST /jobs` validates, stores the job, returns `202` and the client polls the status
endpoint. I used FastAPI's `BackgroundTasks` rather than Celery/Redis: it needs no extra
infrastructure and is enough for this scope. The trade-off (below) is handled with
startup recovery.

**Validation is per recipient, not all-or-nothing.** Request-level problems (missing
`event_name`, empty list, too many recipients, bad date) return `422`. For individual
recipients, an invalid row is stored as a `failed` certificate with a human-readable reason
(`"Validation failed - email: is not a valid email address"`) and the valid rows are still
generated. If *no* recipient is valid the request is rejected with `422` and the list of
errors, and no job is created.

**Failure isolation.** Each certificate is generated inside its own `try/except` and its own
commit. One failure marks only that certificate `failed` (with the error) and increments
`job.failed`; the rest continue. PDFs are written to a temp file and renamed, so a failure
never leaves a half-written file.

**Job state = counters + per-certificate rows.** `succeeded`/`failed` are updated in the same
transaction that updates the certificate, so progress is always consistent and cheap to read
(no `COUNT(*)` per poll).

**Crash/restart recovery.** In-process background tasks die with the process. Because state is
committed per certificate, on startup the app finds jobs still `pending`/`processing` and
finishes the certificates that are still `pending` (already-finished ones are not redone).
This is why the app should run as one worker: two workers would both resume the same job.
Scaling out would mean replacing `BackgroundTasks` with a real queue (Celery/RQ) and claiming
jobs with `SELECT ... FOR UPDATE SKIP LOCKED`.

**Certificate format.** One landscape A4 PDF per recipient, drawn with ReportLab (name, event,
issuer, date, unique certificate ID). Name and event text auto-shrink / wrap to fit.

**Storage.** PDFs live on disk at `STORAGE_DIR/<job_id>/<certificate_id>.pdf`; the DB stores
the relative path. IDs are UUIDs so download URLs aren't guessable by counting.

**SQLite settings.** WAL mode + busy timeout so status polling is not blocked by the
background writer. Tables are created with `create_all` (no migrations - fine for this scope).

## 7. Known limitations

- Names must be Latin-script (the PDF built-in fonts can't render e.g. Bengali/Devanagari);
  such names are rejected with a clear message rather than printed as blank boxes. Supporting
  them means bundling a Unicode TTF font and registering it in `app/certificate.py`.
- No authentication, rate limiting or cleanup of old jobs.
- The ZIP is built in memory (fine for ~1000 small PDFs; stream it for much larger jobs).

## 8. Project layout

```
app/
  main.py         FastAPI app factory + routes
  service.py      create_job, process_job, recover_unfinished_jobs
  validation.py   per-recipient validation
  certificate.py  PDF template/rendering
  models.py       Job, Certificate (SQLAlchemy)
  schemas.py      request/response models
  database.py     engine + session factory
  config.py       settings from environment
tests/            pytest suite
```
