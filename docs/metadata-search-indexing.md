# Metadata Search Indexing

The metadata search path uses PostgreSQL trigram GIN indexes on the searchable text fields.

## Indexed fields

- `apps.metadata.models.Metadata.description`
- `apps.metadata.models.Metadata.sponsor_or_grant`
- `apps.metadata.models.Metadata.category` and `apps.metadata.models.Metadata.subject` as a composite B-tree helper for the join path

The search API still filters with the same user-facing text query behavior, but PostgreSQL can use the trigram indexes for the `icontains` lookups instead of falling back to a full table scan.

## Why trigram GIN instead of a direct SearchVector expression

A direct `SearchVector(...)` expression index failed during migration in PostgreSQL because the generated expression was not immutable in this schema shape. Trigram GIN indexes are stable, migration-safe, and still speed up the search terms used by the API.

## Benchmarking

Run the benchmark command in Docker to compare the default planner against a forced seq-scan baseline:

```bash
docker compose exec django python manage.py migrate
docker compose run --rm django python manage.py benchmark_metadata_search cancer
```

The command prints both query plans with `EXPLAIN ANALYZE` so you can compare execution time before and after the index is present.

## Deployment note

The index migration uses PostgreSQL extension support for trigram indexing and should be applied during a normal migration window. On large staging datasets, run it during a low-traffic window to reduce lock impact.
