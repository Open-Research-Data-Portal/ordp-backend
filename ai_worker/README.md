# ORDP AI Worker

Standalone background worker for ORDP file analysis. Pulls analysis jobs from the Django internal API, streams and inspects uploaded dataset files, validates format and integrity constraints, and posts analysis results back.

## Responsibilities
- Plain inspection only (no LLMs, no embeddings, no external API calls)
- Streaming file downloads directly to isolated temporary storage
- Content-type detection using `libmagic`
- Integrity and limit checks:
  1. `type_from_contents` & `extension_matches`
  2. `checksum_verified` (SHA-256)
  3. `not_empty`
  4. `opens_correctly` (csv, xlsx, pdf, docx, zip)
  5. `password_protected` (pdf, zip, office)
  6. `macros` (docm, xlsm, vbaProject.bin)
  7. `archive_limits` (zip/tar limit inspection without extracting)
  8. `malware_scan` (pluggable scanner interface: disabled by default, supports ClamAV)

## Running Locally

Run the worker directly from its own directory:

```bash
cd ai_worker
pip install -r requirements.txt
python main.py
```

> [!IMPORTANT]
> Always run with `cd ai_worker` then `python main.py`. Do **not** run `python -m ai_worker.main` from the repository root because the backend's `config/` package shadows `ai_worker/config.py`.
>
> The worker reads `AI_BACKEND_URL` (not `ORDP_API_BASE` or `VITE_API_URL`) and only reads environment variables set in its own terminal session, **not** the backend `.env` file. Set `ORDP_WORKER_TOKEN` and `AI_BACKEND_URL` in the worker terminal before starting.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `ORDP_WORKER_TOKEN` | *(Required)* | Secret Bearer token for worker authentication against `/internal/ai-jobs/`. Must match the backend token. Worker exits at startup if unset or placeholder. |
| `AI_BACKEND_URL` | `http://django:8000` | Base URL of the ORDP Django backend (use `http://127.0.0.1:8000` for local dev). |
| `AI_WORKER_ID` | `ai-worker-1` | Unique identifier string for this worker instance. |
| `AI_POLL_INTERVAL_SECONDS` | `5` | Idle sleep duration in seconds when no queued jobs are found. |
| `AI_MAX_JOB_SECONDS` | `300` | Max duration allowed per file check before marking partial. |
| `CLAMAV_ENABLED` | `false` | Enable ClamAV antivirus scanning if true. |

## Running with Docker Compose

The worker is integrated as the `ai-worker` service in `docker-compose.yml`.
