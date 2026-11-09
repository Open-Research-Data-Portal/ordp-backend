from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.datasets.models import Dataset, DatasetFile
from .models import (
    AccessRequest, AccessRequestPermission, Group, GroupMembership,
    GroupPermissionGrant, Permission, PermissionAuditLog, UserPermissionGrant,
)


ENFORCED_PERMISSION_CODES = {
    "dataset.view", "dataset.edit", "dataset.delete", "dataset.download",
    "user.view", "user.permission.grant", "user.permission.revoke",
    "group.create", "group.edit", "group.delete", "group.member.manage",
    "group.permission.manage", "access_request.review", "audit.view",
}


def validate_scope(resource_type, resource_id):
    if resource_type == "platform" and resource_id is None:
        return
    model = {"dataset": Dataset, "dataset_file": DatasetFile}.get(resource_type)
    if not model or not resource_id or not model.objects.filter(pk=resource_id).exists():
        raise ValueError("Select an existing resource with a valid scope.")


def active_grants(user, permission_code=None, resource_type=None, resource_id=None):
    if not user or not user.is_authenticated or not user.is_active:
        return [], []
    now = timezone.now()
    active = Q(revoked_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    direct = UserPermissionGrant.objects.filter(active, user=user)
    inherited = GroupPermissionGrant.objects.filter(
        active, group__archived_at__isnull=True,
        group__memberships__user=user,
    ).distinct()
    if permission_code:
        direct = direct.filter(permission_id=permission_code)
        inherited = inherited.filter(permission_id=permission_code)
    if resource_type:
        direct = direct.filter(resource_type=resource_type, resource_id=resource_id)
        inherited = inherited.filter(resource_type=resource_type, resource_id=resource_id)
    return direct, inherited


def other_permission_manager_exists(*, excluded_direct=None, excluded_group_grant=None,
                                    excluded_group=None, excluded_member=None):
    """Check whether a live manager will remain after one access change."""
    now = timezone.now()
    live = Q(revoked_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    direct = UserPermissionGrant.objects.filter(
        live, permission_id="user.permission.grant", resource_type="platform", user__is_active=True,
    )
    if excluded_direct:
        direct = direct.exclude(pk=excluded_direct.pk)
    if direct.exists():
        return True
    inherited = GroupPermissionGrant.objects.filter(
        live, permission_id="user.permission.grant", resource_type="platform",
        group__archived_at__isnull=True, group__memberships__user__is_active=True,
    )
    if excluded_group_grant:
        inherited = inherited.exclude(pk=excluded_group_grant.pk)
    if excluded_group:
        inherited = inherited.exclude(group=excluded_group)
    if excluded_member:
        if inherited.exclude(group=excluded_member.group).exists():
            return True
        return (inherited.filter(group=excluded_member.group).exists()
                and GroupMembership.objects.filter(group=excluded_member.group, user__is_active=True)
                .exclude(pk=excluded_member.pk).exists())
    return inherited.exists()


def has_permission(user, action, resource_type="platform", resource_id=None):
    direct, inherited = active_grants(user, action, resource_type, resource_id)
    return direct.exists() or inherited.exists()


def effective_permissions(user, resource_type=None, resource_id=None):
    direct, inherited = active_grants(user, resource_type=resource_type, resource_id=resource_id)
    entries = {}
    for grant in direct.select_related("permission"):
        key = (grant.permission_id, grant.resource_type, grant.resource_id)
        row = entries.setdefault(key, {
            "code": grant.permission_id, "name": grant.permission.name,
            "resource_type": grant.resource_type,
            "resource_id": str(grant.resource_id) if grant.resource_id else None,
            "sources": [],
        })
        row["sources"].append({"type": "direct", "id": str(grant.id), "expires_at": grant.expires_at})
    for grant in inherited.select_related("permission", "group"):
        key = (grant.permission_id, grant.resource_type, grant.resource_id)
        row = entries.setdefault(key, {
            "code": grant.permission_id, "name": grant.permission.name,
            "resource_type": grant.resource_type,
            "resource_id": str(grant.resource_id) if grant.resource_id else None,
            "sources": [],
        })
        row["sources"].append({"type": "group", "id": str(grant.id), "group_id": str(grant.group_id), "group_name": grant.group.name, "expires_at": grant.expires_at})
    return list(entries.values())


def audit(actor, action, *, user=None, group=None, permission=None, resource_type="", resource_id=None, metadata=None):
    return PermissionAuditLog.objects.create(
        actor=actor, action=action, target_user=user, target_group=group,
        permission=permission, resource_type=resource_type, resource_id=resource_id,
        metadata=metadata or {},
    )


def ensure_can_manage(actor, authority, permission=None, resource_type=None, resource_id=None):
    if not has_permission(actor, authority):
        raise PermissionError("You do not have permission to manage this access.")
    if permission and not has_permission(actor, "user.manage"):
        if not has_permission(actor, permission.code, resource_type, resource_id):
            raise PermissionError("You cannot delegate a permission you do not hold.")


@transaction.atomic
def grant_to_user(actor, user, permission, resource_type, resource_id=None, expires_at=None, reason=""):
    if permission.code not in ENFORCED_PERMISSION_CODES:
        raise ValueError("This permission is not enforced by an endpoint yet.")
    validate_scope(resource_type, resource_id)
    if permission.resource_type != resource_type:
        raise ValueError("This permission does not apply to that resource type.")
    if expires_at and expires_at <= timezone.now():
        raise ValueError("Expiration must be in the future.")
    ensure_can_manage(actor, "user.permission.grant", permission, resource_type, resource_id)
    now = timezone.now()
    UserPermissionGrant.objects.filter(
        user=user, permission=permission, resource_type=resource_type,
        resource_id=resource_id, revoked_at__isnull=True, expires_at__lte=now,
    ).update(revoked_at=now)
    grant, created = UserPermissionGrant.objects.get_or_create(
        user=user, permission=permission, resource_type=resource_type,
        resource_id=resource_id, revoked_at=None,
        defaults={"granted_by": actor, "expires_at": expires_at},
    )
    if not created:
        raise ValueError("An active direct grant already exists.")
    audit(actor, "user.grant", user=user, permission=permission, resource_type=resource_type,
          resource_id=resource_id, metadata={"reason": reason, "grant_id": str(grant.id)})
    return grant


@transaction.atomic
def grant_to_group(actor, group, permission, resource_type, resource_id=None, expires_at=None, reason=""):
    if permission.code not in ENFORCED_PERMISSION_CODES:
        raise ValueError("This permission is not enforced by an endpoint yet.")
    validate_scope(resource_type, resource_id)
    if group.archived_at or permission.resource_type != resource_type:
        raise ValueError("The group or permission is not available for this resource.")
    if expires_at and expires_at <= timezone.now():
        raise ValueError("Expiration must be in the future.")
    ensure_can_manage(actor, "group.permission.manage", permission, resource_type, resource_id)
    now = timezone.now()
    GroupPermissionGrant.objects.filter(
        group=group, permission=permission, resource_type=resource_type,
        resource_id=resource_id, revoked_at__isnull=True, expires_at__lte=now,
    ).update(revoked_at=now)
    grant, created = GroupPermissionGrant.objects.get_or_create(
        group=group, permission=permission, resource_type=resource_type,
        resource_id=resource_id, revoked_at=None,
        defaults={"granted_by": actor, "expires_at": expires_at},
    )
    if not created:
        raise ValueError("An active group grant already exists.")
    audit(actor, "group.grant", group=group, permission=permission, resource_type=resource_type,
          resource_id=resource_id, metadata={"reason": reason, "grant_id": str(grant.id)})
    return grant


@transaction.atomic
def revoke_user_grant(actor, grant, reason=""):
    ensure_can_manage(actor, "user.permission.revoke")
    if grant.revoked_at:
        raise ValueError("This grant has already been revoked.")
    if grant.permission_id == "user.permission.grant" and grant.resource_type == "platform":
        if not other_permission_manager_exists(excluded_direct=grant):
            raise ValueError("The last permission manager cannot be removed.")
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["revoked_at"])
    audit(actor, "user.revoke", user=grant.user, permission=grant.permission,
          resource_type=grant.resource_type, resource_id=grant.resource_id,
          metadata={"reason": reason, "grant_id": str(grant.id)})


@transaction.atomic
def review_request(actor, access_request, decisions, comment="", membership_decision=None):
    ensure_can_manage(actor, "access_request.review")
    if actor.pk == access_request.requester_id:
        raise PermissionError("You cannot review your own request.")
    if access_request.status != AccessRequest.Status.PENDING:
        raise ValueError("This request is no longer pending.")
    if access_request.expires_at <= timezone.now():
        raise ValueError("This request has expired.")
    if access_request.kind == AccessRequest.Kind.GROUP_MEMBERSHIP:
        ensure_can_manage(actor, "group.member.manage")
        if membership_decision not in ("approved", "rejected") or decisions:
            raise ValueError("Choose approval or rejection for this membership request.")
        if membership_decision == "approved":
            if not access_request.group or access_request.group.archived_at:
                raise ValueError("This group is no longer available.")
            membership, created = GroupMembership.objects.get_or_create(
                group=access_request.group, user=access_request.requester,
                defaults={"added_by": actor},
            )
            if created:
                audit(actor, "group.member.add", group=membership.group, user=membership.user,
                      metadata={"request_id": str(access_request.id)})
        access_request.status = (
            AccessRequest.Status.APPROVED if membership_decision == "approved"
            else AccessRequest.Status.REJECTED
        )
        access_request.review_comment = comment
        access_request.reviewed_by = actor
        access_request.reviewed_at = timezone.now()
        access_request.save(update_fields=["status", "review_comment", "reviewed_by", "reviewed_at"])
        audit(actor, "request.review", user=access_request.requester, group=access_request.group,
              metadata={"request_id": str(access_request.id), "status": access_request.status})
        return access_request
    items = {item.permission_id: item for item in access_request.items.select_related("permission")}
    if set(decisions) != set(items):
        raise ValueError("Decide every requested permission.")
    approved = 0
    for code, result in decisions.items():
        item = items[code]
        decision = result.get("decision")
        if decision not in ("approved", "rejected"):
            raise ValueError("Each decision must be approved or rejected.")
        expiration = result.get("expires_at")
        if decision == "approved":
            if code not in ENFORCED_PERMISSION_CODES:
                raise ValueError("This permission is not enforced by an endpoint yet.")
            is_group = access_request.kind == AccessRequest.Kind.GROUP_PERMISSION
            if is_group:
                if not access_request.group or access_request.group.archived_at:
                    raise ValueError("This group is no longer available.")
                ensure_can_manage(actor, "group.permission.manage", item.permission,
                                  access_request.resource_type, access_request.resource_id)
                existing = GroupPermissionGrant.objects.filter(
                    group=access_request.group, permission=item.permission,
                    resource_type=access_request.resource_type,
                    resource_id=access_request.resource_id, revoked_at__isnull=True,
                ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).first()
            else:
                ensure_can_manage(actor, "user.permission.grant", item.permission,
                                  access_request.resource_type, access_request.resource_id)
                existing = UserPermissionGrant.objects.filter(
                    user=access_request.requester, permission=item.permission,
                    resource_type=access_request.resource_type,
                    resource_id=access_request.resource_id, revoked_at__isnull=True,
                ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).first()
            if existing:
                if expiration and expiration <= timezone.now():
                    raise ValueError("Expiration must be in the future.")
                existing.expires_at = expiration
                existing.save(update_fields=["expires_at"])
                audit(actor, "group.renew" if is_group else "user.renew",
                      group=access_request.group if is_group else None,
                      user=None if is_group else access_request.requester, permission=item.permission,
                      resource_type=access_request.resource_type, resource_id=access_request.resource_id,
                      metadata={"request_id": str(access_request.id)})
            elif is_group:
                grant_to_group(actor, access_request.group, item.permission,
                               access_request.resource_type, access_request.resource_id,
                               expiration, comment)
            else:
                grant_to_user(actor, access_request.requester, item.permission,
                              access_request.resource_type, access_request.resource_id,
                              expiration, comment)
            approved += 1
        item.decision = decision
        item.decision_reason = result.get("reason", "")
        item.approved_expiration = expiration if decision == "approved" else None
        item.save(update_fields=["decision", "decision_reason", "approved_expiration"])
    access_request.status = (
        AccessRequest.Status.APPROVED if approved == len(items) else
        AccessRequest.Status.PARTIAL if approved else AccessRequest.Status.REJECTED
    )
    access_request.review_comment = comment
    access_request.reviewed_by = actor
    access_request.reviewed_at = timezone.now()
    access_request.save(update_fields=["status", "review_comment", "reviewed_by", "reviewed_at"])
    audit(actor, "request.review", user=access_request.requester,
          resource_type=access_request.resource_type, resource_id=access_request.resource_id,
          metadata={"request_id": str(access_request.id), "status": access_request.status})
    return access_request
