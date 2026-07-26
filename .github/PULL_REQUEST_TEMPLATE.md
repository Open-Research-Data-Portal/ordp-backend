## Summary

Sets up the ordp-backend foundation: Django project scaffold with split
settings (dev/staging/production), the six feature apps matching the SRS
functional domains, and Docker Compose configuration to run the full local
dev stack (Django, PostgreSQL 18, MinIO) in containers. Also resolves two
issues found during Docker setup: Postgres 18's new volume mount requirement,
and a host port conflict on 9000.

## SRS / WBS reference

WBS 1.0 (Foundation & Design) — Repository setup, Django scaffolding,
PostgreSQL + MinIO + Nginx dev environment, database schema design

## Type of change

- [x] New feature
- [ ] Bug fix
- [ ] Refactor (no functional change)
- [ ] Tests only
- [ ] Docs / config

## What's included

- Django project (`config/`) with settings split into `base.py`, `dev.py`,
  `staging.py`, `production.py`
- Six feature apps under `apps/`, matching FR ranges from the SRS:
  - `accounts` (FR-1–FR-8), `datasets` (FR-9–FR-20), `metadata` (FR-21–FR-30),
    `search` (FR-31–FR-40), `sharing` (FR-41–FR-50), `admin_panel` (FR-51–FR-62)
- DRF and CORS installed and registered
- `.env.example` documenting all required environment variables
- Docker Compose stack: `db` (Postgres 18), `minio`, `django`, all networked
  together with named volumes for persistence
- `docker/django/Dockerfile`

## Checklist (Definition of Done — PMP 10.4)

- [ ] Reviewed by at least one other team member
- [ ] Unit and/or integration tests written and passing
- [x] New/changed API endpoints, schema, or env vars documented
- [x] Verified end-to-end in shared dev/staging environment (not just locally)
- [x] No secrets or credentials committed (checked against `.env.example`)

## How to test

1. Copy `.env.example` to `.env` and fill in real values
2. Make sure Docker Desktop is running
3. From repo root: `docker compose up --build`
4. Confirm all three containers (db, minio, django) reach a running state —
   db should log "database system is ready to accept connections"
5. In a separate terminal:
   docker compose exec django python manage.py migrate
   docker compose exec django python manage.py createsuperuser
6. Visit http://localhost:8000/admin — log in with the superuser
7. Visit http://localhost:9001 — MinIO console should load

## Notes for reviewer

- Postgres 18 changed its expected volume mount path (now `/var/lib/postgresql`
  instead of `/var/lib/postgresql/data`) — reflected in docker-compose.yml.
- MinIO's default port 9000 conflicted with something already running on my
  machine, so the host-side port is remapped to 9002 (container-internal port
  is still 9000). Console stays on 9001.
- Django isn't wired to MinIO for file storage yet (django-storages/boto3) —
  that comes with the datasets app in Week 3, not part of this PR.
- Base branch for this PR is `development`, not `main`, per our Git workflow.
- All six apps currently have empty urls.py/views.py placeholders — no actual
  endpoints implemented yet, this PR is scaffold only.