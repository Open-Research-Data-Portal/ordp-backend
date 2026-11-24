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

```bash
cd ai_worker
pip install -r requirements.txt
python main.py
```

## Running with Docker Compose

The worker is integrated as the `ai-worker` service in `docker-compose.yml`.
