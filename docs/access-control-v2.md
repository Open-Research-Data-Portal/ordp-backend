# ORDP Version 2 access control

## Status and rollout

This branch introduces a resource-scoped permission engine and management API. It does not delete existing roles, grants, or sharing records. The existing role-based API checks remain in place during migration; `dataset.edit` and `dataset.download` grants are also honored by their existing endpoints. Do not treat this branch as a complete role-free authorization cutover until the remaining legacy endpoints have been mapped and migrated.

The migration creates new tables only. No existing user receives a new permission automatically. The permission catalog is seeded by migration `0002_catalog`; unanswered requests expire after 30 days by default (`ACCESS_REQUEST_EXPIRATION_DAYS`). No production database migration is run by this branch.

## Deploy sequence

1. Back up the database and deploy the backend branch.
2. Run `python manage.py migrate` against the intended environment.
3. As a trusted operator, run `python manage.py bootstrap_permission_manager --email <existing-superuser-email>` once. The command requires an active existing superuser and refuses to run when a direct permission manager exists.
4. Verify `/api/access-control/me/`, catalog, and manager overview with the intended accounts.
5. Deploy the frontend branch and verify a direct grant, group inheritance, a partial request decision, and revocation on a test dataset.
6. Map remaining role and owner checks to explicit permission codes before disabling legacy authorization. Preserve owner and public access behavior in the migration policy.

## Permission evaluation

`has_permission(user, code, resource_type, resource_id)` is deny-by-default for the new system. Active direct grants and active grants inherited from any unarchived group are additive. Revoked and expired grants do not count. Platform grants use no resource ID; dataset grants match one exact dataset UUID. There is no implicit project inheritance or user-level deny override. `effective_permissions` includes direct and group sources and expiry dates.

Only an actor with `user.permission.grant` or `group.permission.manage` may issue grants. Unless they also have `user.manage`, the actor may delegate only permissions they hold for the same resource. Request reviewers cannot review their own requests. Approval writes grants and audit records in one transaction; rejected items create no grants. High-impact confirmation is shown in the frontend.

## API

All paths below are under `/api/access-control/` and require authentication.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `me/`, `catalog/` | Current effective permissions; catalog |
| GET | `overview/`, `users/`, `users/<id>/` | Manager overview and user access |
| POST | `users/<id>/grants/` | Grant direct access |
| DELETE | `user-grants/<uuid>/` | Revoke a direct grant |
| GET, POST | `groups/` | List or create groups |
| GET, PATCH, DELETE | `groups/<uuid>/` | Detail, edit, archive |
| POST, DELETE | `groups/<uuid>/members/<user-id>/` | Add or remove member |
| POST | `groups/<uuid>/grants/` | Grant group access |
| DELETE | `group-grants/<uuid>/` | Revoke group grant |
| GET, POST | `requests/` | List or submit access requests |
| GET | `resources/` | Discover active datasets; ordinary users see published, nonprivate datasets |
| GET, DELETE | `requests/<uuid>/` | View or cancel own pending request |
| POST | `requests/<uuid>/review/` | Decide every requested permission independently |
| GET | `resources/dataset/<uuid>/` | Active grants on one dataset |
| POST | `resources/dataset/<uuid>/matrix/` | Apply matrix changes atomically |
| GET | `audit/` | Recent permission changes |

Grant body: `{ "permission": "dataset.view", "resource_type": "dataset", "resource_id": "<uuid>", "expires_at": null, "reason": "..." }`. Platform grants use `resource_type: "platform"` and `resource_id: null`.

Request body: `{ "resource_type": "dataset", "resource_id": "<uuid>", "permissions": ["dataset.view", "dataset.download"], "justification": "..." }`. Only published, active, nonprivate datasets can be requested through this flow.

Review body: `{ "decisions": [{ "permission": "dataset.view", "decision": "approved", "expires_at": null }, { "permission": "dataset.download", "decision": "rejected", "reason": "..." }], "comment": "..." }`.

## Tests

Run `python manage.py test apps.access_control apps.sharing apps.datasets.test_editing --settings=config.settings.test_access_control`. This uses an in-memory SQLite database for local verification. Verify migrations and constraints again against PostgreSQL in staging before production deployment.

## Known migration work

The legacy ORDP role and owner checks still authorize many existing endpoints. A complete role-free cutover requires an endpoint-by-endpoint permission policy and a backfill plan for existing users, contributors, reviewers, and admins. Project and collection scopes are not enabled because there is no corresponding persistent resource model in this branch. Dataset-file scope is reserved in the schema but has no independent file authorization endpoint yet. Group-membership requests and group creation wizard are not included in this checkpoint.
