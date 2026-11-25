# AI File Check Service

This document describes the ORDP Version 2 AI File Check side service, including its workflow, configuration, worker operation, and API endpoints.

## 1. Overview and Architecture

The AI File Check service runs as an asynchronous, non-blocking side service. It evaluates uploaded files against structural, format, integrity, and safety checks without running language models or external AI APIs.

### Workflow

```
1. File upload completes (finalize_upload in apps/datasets/services/assembly.py).
   -> transaction.on_commit hook calls enqueue_analysis_job (wrapped in try/except).
   -> Creates AnalysisJob with idempotent key "analyse_file:<sha256>".
   -> Creates or reuses FileAnalysis row for the StoredObject.
2. AI Worker queries GET /internal/ai-jobs/next/ (authenticated with ORDP_WORKER_TOKEN).
3. Backend atomically claims the job using select_for_update(skip_locked=True)
   and provides a short-lived presigned download link (15 minutes).
4. Worker streams the file directly to isolated temporary storage on disk.
5. Worker runs the 8 checks sequentially under a strict time limit (AI_MAX_JOB_SECONDS).
6. Worker posts the result to POST /internal/ai-jobs/{id}/result/.
7. Backend updates FileAnalysis for all StoredObjects sharing that checksum.
8. Frontends and consumers read GET /api/files/{id}/analysis/.
```

### File Levels

| Level | File Types |
|---|---|
| **A** | csv, tsv, xlsx, xls, parquet, json-table |
| **B1** | pdf, docx, txt, md |
| **B2** | images, audio, video, geotiff, netcdf, hdf5, dicom |
| **C** | other unrecognized formats |

Content type is detected from binary magic bytes (via `python-magic`), not from file extensions.

---

## 2. Checks Performed

Each check returns `{ "name": "<check>", "result": "pass" | "warn" | "fail" | "skipped", "detail": "..." }`:

1. `type_from_contents` & `extension_matches`: Content inspection via `libmagic`. Warns if extension differs from detected MIME type.
2. `checksum_verified`: Recomputes SHA-256 over streamed bytes and verifies match against `StoredObject.checksum_sha256`.
3. `not_empty`: Verifies file size > 0 and readable bytes.
4. `opens_correctly`: Parses CSV, XLSX, PDF, DOCX, ZIP, or JSON using standard libraries. If unopenable, status is marked `failed` with code `CANNOT_READ`.
5. `password_protected`: Checks if PDF, ZIP, or Office files are encrypted or password protected.
6. `macros`: Detects macro-enabled files (`docm`, `xlsm`, `vbaProject.bin`) and records a warning.
7. `archive_limits`: For ZIP/tar files, inspects metadata **without extracting** against safety limits:
   - Member count <= `AI_MAX_ARCHIVE_FILES` (default 1000)
   - Total uncompressed size <= `AI_MAX_ARCHIVE_UNCOMPRESSED_GB` (default 2 GB)
   - Compression ratio <= `AI_MAX_COMPRESSION_RATIO` (default 100x)
   - Folder nesting depth <= `AI_MAX_NESTING` (default 2)
8. `malware_scan`: Scanner interface. Defaults to `DisabledScanner` (`result: "skipped"`). Configurable with ClamAV (`clamd`) via environment variables. When disabled, the UI shows **"Skipped, not configured"** — never "Zero malware detected".

---

## 3. Environment Variables

### Backend (`config/settings/base.py`)

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_DB` | `ordp` | PostgreSQL database name. |
| `POSTGRES_USER` | `ordp` | PostgreSQL username. |
| `POSTGRES_PASSWORD` | `""` | PostgreSQL password. |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host (use `localhost` for local dev, `db` inside Docker). |
| `POSTGRES_PORT` | `5432` | PostgreSQL port. |
| `POSTGRES_SSLMODE` | `prefer` | SSL mode (`disable` for local dev without SSL). |
| `ORDP_WORKER_TOKEN` | `""` (empty) | Secret Bearer token for worker authentication against internal endpoints. Must match `ORDP_WORKER_TOKEN` in the worker. If unset, empty, or set to `changeme-worker-token`, all worker requests are rejected with 401. |
| `OBJECT_STORAGE_INTERNAL_URL` | `""` | Optional URL prefix rewrite so the AI worker container can reach storage. Set to the internal storage hostname if the presigned URL hostname is unreachable from the worker container (e.g., `http://minio:9000`). |

### Worker (`ai_worker/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `ORDP_WORKER_TOKEN` | `""` (empty) | Must match the backend `ORDP_WORKER_TOKEN`. Worker exits immediately on startup if unset, empty, or set to `changeme-worker-token`. |
| `AI_BACKEND_URL` | `http://django:8000` | Backend base URL for worker API calls. Use `http://localhost:8000` for local dev. |
| `AI_WORKER_ID` | `ai-worker-1` | Identifier for the running worker instance. |
| `AI_POLL_INTERVAL_SECONDS` | `5` | Idle sleep duration when no jobs are queued. |
| `AI_MAX_JOB_SECONDS` | `300` | Max duration allowed per file check before marking partial. |
| `AI_MAX_ARCHIVE_FILES` | `1000` | Max file count inside zip/tar archive. |
| `AI_MAX_ARCHIVE_UNCOMPRESSED_GB` | `2` | Max uncompressed archive volume in GB. |
| `AI_MAX_COMPRESSION_RATIO` | `100` | Max compression ratio before flagging zip bomb. |
| `AI_MAX_NESTING` | `2` | Max folder nesting depth in archive. |
| `CLAMAV_ENABLED` | `false` | Set to `true` to enable ClamAV malware scanning. |
| `CLAMAV_HOST` | `""` | Hostname/IP for clamd TCP socket. |
| `CLAMAV_PORT` | `3310` | Port for clamd TCP socket. |

> [!NOTE]
> **Frontend file ID**: The frontend passes `DatasetFile.id` from the dataset API. The backend `GET /api/files/{id}/analysis/` accepts both `StoredObject.id` and `DatasetFile.id` (fallback lookup) so both V1 and V2 file identifiers work correctly.

---

## 4. Running Locally Without Docker (Windows)

> [!IMPORTANT]
> These instructions use a local PostgreSQL instance. No Docker required.

### Prerequisites

- Python 3.11+ in a virtual environment
- PostgreSQL 15+ running locally (default port 5432)
- An `.env` file or environment variables set as below
- Node.js 18+ (for the frontend)

### 4.1 Backend Setup

```powershell
cd C:\Internships\ORDP\ordp-backend

# Create the ordp user and ordp_dev database (run once)
# In a psql session as postgres superuser:
#   CREATE USER ordp WITH CREATEDB PASSWORD '<your-postgres-password>';
#   CREATE DATABASE ordp_dev OWNER ordp;

# Set environment variables (PowerShell)
$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_DB       = "ordp_dev"
$env:POSTGRES_USER     = "ordp"
$env:POSTGRES_PASSWORD = "<your-postgres-password>"
$env:POSTGRES_SSLMODE  = "disable"
$env:ORDP_WORKER_TOKEN = "your-secure-worker-token"
$env:EMAIL_BACKEND     = "django.core.mail.backends.console.EmailBackend"  # Prints verification emails to terminal
$env:DJANGO_SETTINGS_MODULE = "config.settings.dev"

# Install dependencies
pip install -r requirements/dev.txt

# Apply migrations
python manage.py migrate

# Start the server
python manage.py runserver 0.0.0.0:8000
```

### 4.2 AI Worker Setup (Windows)

> [!IMPORTANT]
> Run the worker locally with `cd ai_worker` then `python main.py`. Do **not** run `python -m ai_worker.main` from the repository root because the backend's `config/` package shadows `ai_worker/config.py`.
>
> The worker reads `AI_BACKEND_URL` (not `ORDP_API_BASE` or `VITE_API_URL`) and only reads environment variables set in its own terminal session, **not** the backend `.env`.

```powershell
cd C:\Internships\ORDP\ordp-backend\ai_worker

# Install worker-only dependencies
pip install -r requirements.txt

# On Windows, python-magic needs the bundled DLL version:
pip install python-magic-bin

# Set environment variables in the worker terminal
$env:ORDP_WORKER_TOKEN    = "your-secure-worker-token"
$env:AI_BACKEND_URL       = "http://127.0.0.1:8000"
$env:AI_WORKER_ID         = "ai-worker-1"
$env:CLAMAV_ENABLED       = "false"

# Start the worker
python main.py
```

> [!TIP]
> On Linux/Mac, install system `libmagic` (`apt install libmagic1` or `brew install libmagic`) instead of `python-magic-bin`.

### 4.3 Frontend Setup (Windows)

```powershell
cd C:\Internships\ORDP\ordp-frontend

# Set backend proxy target before starting Vite dev server
$env:VITE_PROXY_TARGET = "http://127.0.0.1:8000"

npm install
npm run dev
```

### 4.4 Running the Django Tests

```powershell
cd C:\Internships\ORDP\ordp-backend

$env:POSTGRES_HOST     = "localhost"
$env:POSTGRES_DB       = "ordp_dev"
$env:POSTGRES_USER     = "ordp"
$env:POSTGRES_PASSWORD = "<your-postgres-password>"   # use your actual ordp user password
$env:POSTGRES_SSLMODE  = "disable"
$env:ORDP_WORKER_TOKEN = "test-token-for-tests"
$env:DJANGO_SETTINGS_MODULE = "config.settings.dev"

python manage.py test apps.ai_integration.tests --verbosity=2
```

The test suite includes 10 tests:

| Test | Type | What it verifies |
|---|---|---|
| `test_enqueue_idempotent` | TestCase | Enqueuing the same key twice creates one job |
| `test_claim_next_job` | TestCase | Claim marks job as running and locks it |
| `test_claim_next_no_jobs` | TestCase | Returns None when queue is empty |
| `test_complete_job` | TestCase | Complete sets status=done on job and FileAnalysis |
| `test_fail_and_retry` | TestCase | fail_and_retry increments attempts, sets run_after |
| `test_fail_max_attempts` | TestCase | Job becomes failed after max_attempts |
| `test_save_result_idempotent` | TestCase | Posting same result twice returns saved=true both times |
| `test_two_workers_claim_different_jobs` | **TransactionTestCase** | Two concurrent workers claim different jobs (requires PostgreSQL, tests `SELECT FOR UPDATE SKIP LOCKED`) |
| `test_internal_next_job_endpoint` | TestCase | GET /internal/ai-jobs/next/ returns 204 when empty |
| `test_public_analysis_endpoint` | TestCase | GET /api/files/{id}/analysis/ returns 404 for non-existent file |

> [!NOTE]
> `test_two_workers_claim_different_jobs` uses `TransactionTestCase` which creates and destroys a real PostgreSQL test database. It **requires PostgreSQL** and will not run on SQLite.

### 4.4 Polling Behaviour

The frontend badge polls the analysis endpoint every **5 seconds** (minimum) while status is `queued` or `running`. Polling stops automatically when status becomes `done`, `partial`, or `failed`.

---

## 5. API Endpoints and Curl Examples

### Internal: Claim Next Job
* **Route**: `GET /internal/ai-jobs/next/`
* **Headers**: `Authorization: Bearer <ORDP_WORKER_TOKEN>`
* **Status**: `200 OK` (job claimed) or `204 No Content` (queue empty)

```bash
curl -X GET "http://localhost:8000/internal/ai-jobs/next/" \
  -H "Authorization: Bearer changeme-worker-token" \
  -H "X-Worker-Id: ai-worker-1"
```

Response (200 OK):
```json
{
  "id": "e9b5f448-438d-4cb0-a33f-67f7e9a8f4b2",
  "type": "analyse_file",
  "file_id": "7f0d2c38-8a5f-4d43-9831-50e50f75e9b3",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "size": 12345,
  "filename": "survey_data.csv",
  "file_url": "http://storage:9000/ordp-datasets/objects/...?..."
}
```

---

### Internal: Post Job Result
* **Route**: `POST /internal/ai-jobs/{id}/result/`
* **Headers**: `Authorization: Bearer <ORDP_WORKER_TOKEN>`, `Content-Type: application/json`

```bash
curl -X POST "http://localhost:8000/internal/ai-jobs/e9b5f448-438d-4cb0-a33f-67f7e9a8f4b2/result/" \
  -H "Authorization: Bearer changeme-worker-token" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "done",
    "level": "A",
    "detected_type": "csv",
    "extension_matches": true,
    "coverage": "fully_analysed",
    "checks": [
      {"name": "type_from_contents", "result": "pass", "detail": "Detected csv (text/csv)"},
      {"name": "extension_matches", "result": "pass", "detail": "Content type matches filename"},
      {"name": "checksum_verified", "result": "pass", "detail": "SHA-256 verified"},
      {"name": "not_empty", "result": "pass", "detail": "File is not empty (12345 bytes)"},
      {"name": "opens_correctly", "result": "pass", "detail": "Successfully parsed CSV format"},
      {"name": "password_protected", "result": "pass", "detail": "No password protection detected"},
      {"name": "macros", "result": "pass", "detail": "No macros detected"},
      {"name": "archive_limits", "result": "skipped", "detail": "Not a zip or tar archive"},
      {"name": "malware_scan", "result": "skipped", "detail": "Malware scan is disabled"}
    ],
    "warnings": [],
    "technical": {
      "size_bytes": 12345,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    "error": null
  }'
```

Response:
```json
{
  "saved": true
}
```

---

### Public: View File Analysis
* **Route**: `GET /api/files/{id}/analysis/`
* **Permissions**: Follows shared dataset access permissions. Returns `404 Not Found` if file does not exist or caller cannot see the dataset.
* **Status**: `200 OK`
* **Note**: `{id}` can be either a `StoredObject.id` or a `DatasetFile.id` — the backend resolves both.

```bash
curl -X GET "http://localhost:8000/api/files/7f0d2c38-8a5f-4d43-9831-50e50f75e9b3/analysis/" \
  -H "Authorization: Bearer <USER_JWT_TOKEN>"
```

Response (Completed — scanner disabled):
```json
{
  "file_id": "7f0d2c38-8a5f-4d43-9831-50e50f75e9b3",
  "status": "done",
  "level": "A",
  "coverage": "fully_analysed",
  "detected_type": "csv",
  "checks": [
    {"name": "type_from_contents", "result": "pass", "detail": "Detected csv (text/csv)"},
    {"name": "checksum_verified", "result": "pass", "detail": "SHA-256 verified"},
    {"name": "not_empty", "result": "pass", "detail": "File is not empty (12345 bytes)"},
    {"name": "opens_correctly", "result": "pass", "detail": "Successfully parsed CSV format"},
    {"name": "malware_scan", "result": "skipped", "detail": "Malware scan is disabled"}
  ],
  "warnings": [],
  "technical": {
    "size_bytes": 12345,
    "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  },
  "error": null
}
```

Response (While Running):
```json
{
  "file_id": "7f0d2c38-8a5f-4d43-9831-50e50f75e9b3",
  "status": "running",
  "level": null,
  "coverage": null,
  "detected_type": null,
  "checks": [],
  "warnings": [],
  "technical": {}
}
```
