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
   - Total uncompressed size <= `AI_MAX_ARCHIVE_UNCOMPRESSED_GB` (default 2GB)
   - Compression ratio <= `AI_MAX_COMPRESSION_RATIO` (default 100x)
   - Folder nesting depth <= `AI_MAX_NESTING` (default 2)
8. `malware_scan`: Scanner interface. Defaults to `DisabledScanner` (skipped). Configurable with ClamAV (`clamd`) via environment variables.

---

## 3. Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ORDP_WORKER_TOKEN` | `changeme-worker-token` | Secret Bearer token for worker authentication against internal endpoints. |
| `AI_BACKEND_URL` | `http://django:8000` | Backend base URL for worker API calls. |
| `AI_WORKER_ID` | `ai-worker-1` | Identifier for the running worker instance. |
| `AI_POLL_INTERVAL_SECONDS` | `5` | Idle sleep duration when no jobs are queued. |
| `AI_MAX_JOB_SECONDS` | `300` | Max duration allowed per file check before marking partial. |
| `AI_MAX_ARCHIVE_FILES` | `1000` | Max file count inside zip/tar archive. |
| `AI_MAX_ARCHIVE_UNCOMPRESSED_GB`| `2` | Max uncompressed archive volume. |
| `AI_MAX_COMPRESSION_RATIO` | `100` | Max compression ratio before flagging zip bomb. |
| `AI_MAX_NESTING` | `2` | Max folder nesting depth in archive. |
| `CLAMAV_ENABLED` | `False` | Enable ClamAV malware scanning. |
| `CLAMAV_HOST` | `""` | Hostname/IP for clamd TCP socket. |
| `CLAMAV_PORT` | `3310` | Port for clamd TCP socket. |
| `OBJECT_STORAGE_INTERNAL_URL` | `""` | Optional URL rewrite for containers to access storage. |

---

## 4. How to Run the Worker

### Using Docker Compose

The `ai-worker` service is automatically started with Docker Compose:

```bash
docker compose up --build
```

### Running Standalone / Locally

```bash
cd ai_worker
pip install -r requirements.txt
export ORDP_WORKER_TOKEN="your-worker-token"
export AI_BACKEND_URL="http://localhost:8000"
python main.py
```

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

```bash
curl -X GET "http://localhost:8000/api/files/7f0d2c38-8a5f-4d43-9831-50e50f75e9b3/analysis/" \
  -H "Authorization: Bearer <USER_JWT_TOKEN>"
```

Response (Completed):
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
    {"name": "opens_correctly", "result": "pass", "detail": "Successfully parsed CSV format"}
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
