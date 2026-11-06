from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.datasets.models import Dataset
from apps.notifications.models import Notification
from apps.notifications.services import notify
from .models import (
    AccessRequest, AccessRequestPermission, Group, GroupMembership,
    GroupPermissionGrant, Permission, PermissionAuditLog, UserPermissionGrant,
)
from .serializers import AccessRequestSerializer, GrantSerializer, GroupSerializer, RequestCreateSerializer, ReviewSerializer
from .services import audit, effective_permissions, ensure_can_manage, grant_to_user, has_permission, review_request, revoke_user_grant

User = get_user_model()


def denied():
    return Response({"detail": "You do not have permission to perform this action."}, status=403)


def grant_data(grant):
    return {"id": str(grant.id), "permission": grant.permission_id,
            "resource_type": grant.resource_type,
            "resource_id": str(grant.resource_id) if grant.resource_id else None,
            "expires_at": grant.expires_at, "revoked_at": grant.revoked_at,
            "granted_at": grant.granted_at}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def catalog(request):
    return Response(list(Permission.objects.order_by("resource_type", "name").values(
        "code", "name", "description", "resource_type", "action")))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_permissions(request):
    return Response({"effective": effective_permissions(request.user),
                     "can_view_users": has_permission(request.user, "user.view"),
                     "can_review_requests": has_permission(request.user, "access_request.review"),
                     "can_view_audit": has_permission(request.user, "audit.view")})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def discover_resources(request):
    datasets = Dataset.objects.filter(is_active=True)
    if not has_permission(request.user, "user.view"):
        datasets = datasets.filter(
            status=Dataset.Status.PUBLISHED,
        ).exclude(visibility=Dataset.Visibility.PRIVATE)
    return Response(list(datasets.order_by("title").values("id", "title", "status", "visibility")[:500]))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def overview(request):
    if not has_permission(request.user, "user.view"):
        return denied()
    active = Q(revoked_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    return Response({
        "total_users": User.objects.count(),
        "active_users": User.objects.filter(is_active=True).count(),
        "total_groups": Group.objects.filter(archived_at__isnull=True).count(),
        "pending_requests": AccessRequest.objects.filter(status="pending").count(),
        "active_direct_grants": UserPermissionGrant.objects.filter(active).count(),
        "active_group_grants": GroupPermissionGrant.objects.filter(active, group__archived_at__isnull=True).count(),
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def users(request):
    if not has_permission(request.user, "user.view"):
        return denied()
    qs = User.objects.all().order_by("id")
    if request.query_params.get("q"):
        q = request.query_params["q"].strip()
        qs = qs.filter(Q(email__icontains=q) | Q(username__icontains=q) | Q(profile__full_name__icontains=q))
    if request.query_params.get("status") == "active":
        qs = qs.filter(is_active=True)
    elif request.query_params.get("status") == "inactive":
        qs = qs.filter(is_active=False)
    if request.query_params.get("group"):
        qs = qs.filter(access_memberships__group_id=request.query_params["group"])
    rows = []
    for user in qs.select_related("profile")[:100]:
        rows.append({"id": user.pk, "name": user.profile.full_name if hasattr(user, "profile") else user.username,
                     "email": user.email, "is_active": user.is_active,
                     "groups": list(user.access_memberships.filter(group__archived_at__isnull=True).values("group_id", "group__name")),
                     "direct_count": user.direct_permission_grants.filter(revoked_at__isnull=True).count()})
    return Response(rows)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def user_detail(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if user.pk != request.user.pk and not has_permission(request.user, "user.view"):
        return denied()
    direct = UserPermissionGrant.objects.filter(user=user, revoked_at__isnull=True).select_related("permission")
    return Response({"id": user.pk, "name": user.profile.full_name if hasattr(user, "profile") else user.username,
                     "email": user.email, "is_active": user.is_active,
                     "groups": list(user.access_memberships.filter(group__archived_at__isnull=True).values("group_id", "group__name")),
                     "direct": [grant_data(g) for g in direct],
                     "effective": effective_permissions(user)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def user_grants(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    serializer = GrantSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        grant = grant_to_user(request.user, user, **serializer.validated_data)
    except PermissionError:
        return denied()
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(grant_data(grant), status=201)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def user_grant_detail(request, grant_id):
    grant = get_object_or_404(UserPermissionGrant, pk=grant_id)
    try:
        revoke_user_grant(request.user, grant, request.data.get("reason", ""))
    except PermissionError:
        return denied()
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(status=204)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def groups(request):
    if request.method == "GET":
        if not has_permission(request.user, "user.view"):
            return denied()
        qs = Group.objects.filter(archived_at__isnull=True).annotate(
            member_count=Count("memberships", distinct=True),
            permission_count=Count("permission_grants", filter=Q(permission_grants__revoked_at__isnull=True), distinct=True),
        ).order_by("name")
        return Response(GroupSerializer(qs, many=True).data)
    if not has_permission(request.user, "group.create"):
        return denied()
    serializer = GroupSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    group = serializer.save(created_by=request.user)
    audit(request.user, "group.create", group=group)
    return Response(GroupSerializer(group).data, status=201)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def group_detail(request, group_id):
    group = get_object_or_404(Group, pk=group_id, archived_at__isnull=True)
    if request.method == "GET":
        if not has_permission(request.user, "user.view"):
            return denied()
        return Response({"group": GroupSerializer(group).data,
                         "members": list(group.memberships.select_related("user").values("user_id", "user__email", "user__username")),
                         "grants": [grant_data(g) for g in group.permission_grants.filter(revoked_at__isnull=True)]})
    authority = "group.edit" if request.method == "PATCH" else "group.delete"
    if not has_permission(request.user, authority):
        return denied()
    if request.method == "PATCH":
        serializer = GroupSerializer(group, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        audit(request.user, "group.edit", group=group)
        return Response(GroupSerializer(group).data)
    if group.permission_grants.filter(permission_id="user.permission.grant", revoked_at__isnull=True).exists():
        return Response({"detail": "Remove permission-manager access safely before archiving this group."}, status=400)
    group.archived_at = timezone.now()
    group.save(update_fields=["archived_at"])
    audit(request.user, "group.archive", group=group)
    return Response(status=204)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def group_member(request, group_id, user_id):
    group = get_object_or_404(Group, pk=group_id, archived_at__isnull=True)
    user = get_object_or_404(User, pk=user_id)
    if not has_permission(request.user, "group.member.manage"):
        return denied()
    if request.method == "POST":
        membership, created = GroupMembership.objects.get_or_create(group=group, user=user, defaults={"added_by": request.user})
        if created:
            audit(request.user, "group.member.add", user=user, group=group)
        return Response({"id": membership.pk, "created": created}, status=201 if created else 200)
    if group.permission_grants.filter(permission_id="user.permission.grant", revoked_at__isnull=True).exists() and has_permission(user, "user.permission.grant"):
        return Response({"detail": "Remove permission-manager access safely before removing this member."}, status=400)
    GroupMembership.objects.filter(group=group, user=user).delete()
    audit(request.user, "group.member.remove", user=user, group=group)
    return Response(status=204)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def group_grants(request, group_id):
    group = get_object_or_404(Group, pk=group_id, archived_at__isnull=True)
    serializer = GrantSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    value = serializer.validated_data
    try:
        ensure_can_manage(request.user, "group.permission.manage", value["permission"], value["resource_type"], value.get("resource_id"))
    except PermissionError:
        return denied()
    if value.get("expires_at") and value["expires_at"] <= timezone.now():
        return Response({"detail": "Expiration must be in the future."}, status=400)
    with transaction.atomic():
        GroupPermissionGrant.objects.filter(group=group, permission=value["permission"],
            resource_type=value["resource_type"], resource_id=value.get("resource_id"),
            revoked_at__isnull=True, expires_at__lte=timezone.now()).update(revoked_at=timezone.now())
        if group.permission_grants.filter(permission=value["permission"], resource_type=value["resource_type"], resource_id=value.get("resource_id"), revoked_at__isnull=True).exists():
            return Response({"detail": "An active group grant already exists."}, status=400)
        grant = GroupPermissionGrant.objects.create(group=group, permission=value["permission"],
            resource_type=value["resource_type"], resource_id=value.get("resource_id"),
            granted_by=request.user, expires_at=value.get("expires_at"))
        audit(request.user, "group.grant", group=group, permission=grant.permission,
              resource_type=grant.resource_type, resource_id=grant.resource_id,
              metadata={"reason": value.get("reason", ""), "grant_id": str(grant.id)})
    return Response(grant_data(grant), status=201)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def group_grant_detail(request, grant_id):
    grant = get_object_or_404(GroupPermissionGrant, pk=grant_id)
    if not has_permission(request.user, "group.permission.manage"):
        return denied()
    if grant.revoked_at:
        return Response({"detail": "This grant has already been revoked."}, status=400)
    if grant.permission_id == "user.permission.grant":
        return Response({"detail": "Permission-manager access must be transferred before revoking this group grant."}, status=400)
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["revoked_at"])
    audit(request.user, "group.revoke", group=grant.group, permission=grant.permission,
          resource_type=grant.resource_type, resource_id=grant.resource_id,
          metadata={"grant_id": str(grant.id)})
    return Response(status=204)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def access_requests(request):
    if request.method == "GET":
        AccessRequest.objects.filter(status="pending", expires_at__lte=timezone.now()).update(status="expired")
        qs = AccessRequest.objects.select_related("requester__profile").prefetch_related("items__permission").order_by("-created_at")
        if not has_permission(request.user, "access_request.review"):
            qs = qs.filter(requester=request.user)
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(AccessRequestSerializer(qs[:100], many=True).data)
    serializer = RequestCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    value = serializer.validated_data
    with transaction.atomic():
        access_request = AccessRequest.objects.create(
            requester=request.user, resource_type=value["resource_type"], resource_id=value.get("resource_id"),
            justification=value["justification"])
        AccessRequestPermission.objects.bulk_create([
            AccessRequestPermission(request=access_request, permission_id=code)
            for code in value["permissions"]
        ])
        audit(request.user, "request.create", user=request.user,
              resource_type=access_request.resource_type, resource_id=access_request.resource_id,
              metadata={"request_id": str(access_request.id)})
    return Response(AccessRequestSerializer(access_request).data, status=201)


@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def access_request_detail(request, request_id):
    AccessRequest.objects.filter(pk=request_id, status="pending", expires_at__lte=timezone.now()).update(status="expired")
    obj = get_object_or_404(AccessRequest.objects.select_related("requester__profile").prefetch_related("items__permission"), pk=request_id)
    if obj.requester_id != request.user.pk and not has_permission(request.user, "access_request.review"):
        return denied()
    if request.method == "GET":
        return Response(AccessRequestSerializer(obj).data)
    if obj.requester_id != request.user.pk or obj.status != AccessRequest.Status.PENDING:
        return denied()
    obj.status = AccessRequest.Status.CANCELLED
    obj.save(update_fields=["status"])
    audit(request.user, "request.cancel", user=request.user,
          resource_type=obj.resource_type, resource_id=obj.resource_id,
          metadata={"request_id": str(obj.id)})
    return Response(status=204)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def access_request_review(request, request_id):
    AccessRequest.objects.filter(pk=request_id, status="pending", expires_at__lte=timezone.now()).update(status="expired")
    obj = get_object_or_404(AccessRequest.objects.select_related("requester__profile").prefetch_related("items__permission"), pk=request_id)
    serializer = ReviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    decisions = {row["permission"].code: {"decision": row["decision"],
                 "reason": row.get("reason", ""), "expires_at": row.get("expires_at")}
                 for row in serializer.validated_data["decisions"]}
    try:
        obj = review_request(request.user, obj, decisions, serializer.validated_data.get("comment", ""))
    except PermissionError:
        return denied()
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    notify(obj.requester, Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
           f'Your permission request is {obj.get_status_display().lower()}. {obj.review_comment}'.strip(),
           link_path=f"/access-management/requests/{obj.id}")
    return Response(AccessRequestSerializer(obj).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def resource_permissions(request, resource_type, resource_id):
    if not has_permission(request.user, "user.view"):
        return denied()
    if resource_type != "dataset":
        return Response({"detail": "Only dataset resources are available here."}, status=400)
    dataset = get_object_or_404(Dataset, pk=resource_id)
    active = Q(revoked_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    direct = UserPermissionGrant.objects.filter(active, resource_type="dataset", resource_id=dataset.id)
    groups = GroupPermissionGrant.objects.filter(active, resource_type="dataset", resource_id=dataset.id, group__archived_at__isnull=True)
    return Response({"resource": {"id": str(dataset.id), "title": dataset.title},
                     "users": [{"user_id": g.user_id, **grant_data(g)} for g in direct],
                     "groups": [{"group_id": str(g.group_id), **grant_data(g)} for g in groups]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_resource_matrix(request, resource_type, resource_id):
    if resource_type != "dataset":
        return Response({"detail": "Only dataset permissions are supported."}, status=400)
    get_object_or_404(Dataset, pk=resource_id)
    edits = request.data.get("edits")
    if not isinstance(edits, list) or not edits:
        return Response({"detail": "Provide at least one permission change."}, status=400)
    if len(edits) > 100:
        return Response({"detail": "Too many changes in one request."}, status=400)
    seen = set()
    prepared = []
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("type") not in ("user", "group") or not isinstance(edit.get("selected"), bool):
            return Response({"detail": "Invalid matrix change."}, status=400)
        permission = Permission.objects.filter(code=edit.get("permission"), resource_type="dataset").first()
        if not permission:
            return Response({"detail": "Unknown dataset permission."}, status=400)
        try:
            subject = (User.objects.filter(pk=edit.get("id")).first() if edit["type"] == "user"
                       else Group.objects.filter(pk=edit.get("id"), archived_at__isnull=True).first())
        except (ValueError, TypeError, DjangoValidationError):
            subject = None
        if not subject:
            return Response({"detail": "User or group not found."}, status=400)
        key = (edit["type"], str(subject.pk), permission.code)
        if key in seen:
            return Response({"detail": "Duplicate matrix change."}, status=400)
        seen.add(key)
        try:
            ensure_can_manage(request.user,
                "user.permission.grant" if edit["type"] == "user" and edit["selected"] else
                "user.permission.revoke" if edit["type"] == "user" else "group.permission.manage",
                permission if edit["selected"] else None, "dataset", resource_id)
        except PermissionError:
            return denied()
        prepared.append((edit, subject, permission))
    with transaction.atomic():
        for edit, subject, permission in prepared:
            if edit["type"] == "user":
                existing = UserPermissionGrant.objects.filter(user=subject, permission=permission,
                    resource_type="dataset", resource_id=resource_id, revoked_at__isnull=True).filter(
                        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).first()
                if edit["selected"] and not existing:
                    grant_to_user(request.user, subject, permission, "dataset", resource_id)
                elif not edit["selected"] and existing:
                    revoke_user_grant(request.user, existing)
            else:
                existing = GroupPermissionGrant.objects.filter(group=subject, permission=permission,
                    resource_type="dataset", resource_id=resource_id, revoked_at__isnull=True).filter(
                        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).first()
                if edit["selected"] and not existing:
                    GroupPermissionGrant.objects.filter(group=subject, permission=permission,
                        resource_type="dataset", resource_id=resource_id, revoked_at__isnull=True,
                        expires_at__lte=timezone.now()).update(revoked_at=timezone.now())
                    GroupPermissionGrant.objects.create(group=subject, permission=permission,
                        resource_type="dataset", resource_id=resource_id, granted_by=request.user)
                    audit(request.user, "group.grant", group=subject, permission=permission,
                          resource_type="dataset", resource_id=resource_id)
                elif not edit["selected"] and existing:
                    existing.revoked_at = timezone.now()
                    existing.save(update_fields=["revoked_at"])
                    audit(request.user, "group.revoke", group=subject, permission=permission,
                          resource_type="dataset", resource_id=resource_id)
    return Response({"updated": len(prepared)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def audit_history(request):
    if not has_permission(request.user, "audit.view"):
        return denied()
    qs = PermissionAuditLog.objects.select_related("actor", "target_user", "target_group", "permission").order_by("-timestamp")[:100]
    return Response([{"id": row.pk, "actor": row.actor.email if row.actor else None,
                      "target_user": row.target_user.email if row.target_user else None,
                      "target_group": row.target_group.name if row.target_group else None,
                      "permission": row.permission_id, "resource_type": row.resource_type,
                      "resource_id": str(row.resource_id) if row.resource_id else None,
                      "action": row.action, "timestamp": row.timestamp,
                      "metadata": row.metadata} for row in qs])
