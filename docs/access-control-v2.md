# ORDP Version 2 access control

## Status and rollout

This branch provides a resource-scoped permission engine and management API. Authorization for protected APIs uses `has_permission` (and resource ownership where the product requires it). Legacy `UserRole` rows may still exist as profile metadata after `backfill_roles_to_grants`, but role classes (`IsAdminOnly`, `IsReviewerOrAdmin`, `IsReviewerOnly`) now resolve to V2 platform permission codes.

Legacy share entitlements are migrated into `UserPermissionGrant` (`dataset.view` + `dataset.download`). The old share review queue/vote/owner-decision endpoints return **410** and point clients at `/api/access-control/requests/`. Claim-access tokens remain supported and write V2 grants on claim.

Frontend management UI is a separate milestone.

## Deploy sequence

1. Back up the database and deploy the backend branch.
2. Run `python manage.py migrate`.
3. Bootstrap once if needed: `python manage.py bootstrap_permission_manager --email <superuser-email>`.
4. Backfill shares: `python manage.py migrate_share_permissions_to_grants`.
5. Backfill roles/flags: `python manage.py backfill_roles_to_grants`.
6. Optionally drain pending legacy share requests: `python manage.py drain_legacy_access_requests` (or `--approve-pending`).
7. Verify `/api/access-control/me/`, catalog, overview, download, and request review.
8. Deploy frontend when the V2 access-management UI is ready.

## Permission evaluation

`has_permission(user, code, resource_type, resource_id)` is deny-by-default. Active direct grants and grants inherited from unarchived groups are additive. Revoked and expired grants do not count.

Additional rules:

- Platform-scoped catalog codes (e.g. `dataset.create`, `dataset.approve`) are evaluated on `resource_type=platform`.
- `project.view` on a project may grant `dataset.view` on linked datasets (View only; edit/delete/download do not inherit).
- `dataset_file.view` / `dataset_file.download` also succeed when the user holds the matching dataset view/download grant for the file’s parent dataset.

## Enforced catalog (grantable)

Dataset: `view`, `edit`, `delete`, `download`, `upload`, `metadata.edit`, `version.view`, `version.create`, `version.restore`, `approve` (platform), `publish` (platform), `create` (platform).

User/group: `user.view`, `user.manage`, `user.permission.grant`, `user.permission.revoke`, `group.create|edit|delete|member.manage|permission.manage`.

Access/audit: `access_request.create|view|review|approve|reject`, `audit.view`, `audit.export`.

Project/collection/file: `project.*`, `collection.*`, `dataset_file.view`, `dataset_file.download`.

## API

All paths below are under `/api/access-control/` and require authentication.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `me/`, `catalog/` | Effective permissions; catalog with `enforced` |
| GET | `my-resources/` | Datasets with current grants |
| GET | `overview/`, `users/`, `users/<id>/` | Manager overview |
| POST | `users/<id>/grants/`, `users/<id>/grants/bulk/` | Direct grants |
| DELETE | `user-grants/<uuid>/` | Revoke direct grant |
| GET, POST | `groups/`, `group-directory/` | Groups |
| GET, PATCH, DELETE | `groups/<uuid>/` | Detail / edit / archive |
| GET, POST | `requests/` | List or create access requests |
| POST | `requests/<uuid>/review/` | Partial approval decisions |
| GET | `resources/`, `resources/dataset/<uuid>/`, matrix | Discovery and matrix |
| GET | `audit/`, `audit/export/`, `audit/user-management/` | Audit |
| GET, POST | `projects/`, `collections/` | Minimal project/collection resources |

Sharing download remains at `/api/sharing/<uuid>/download/` (V2 grants + owner/contributor free access). Claim: `/api/sharing/claim-access/<token>/` writes V2 grants.

Retired (410): `/api/sharing/access-requests/queue/`, `.../vote/`, `.../owner-decision/`.

## Tests

```bash
python manage.py test apps.access_control apps.sharing apps.datasets.test_editing --settings=config.settings.test_access_control
```

## Known out of scope

- Frontend §8 management UI
- Hard group delete (archive only)
- Legacy share owner + reviewer quorum (replaced by V2 request review)
