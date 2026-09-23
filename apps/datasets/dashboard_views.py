from django.db.models import Q, Sum, F
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.accounts.models import ActivityLog
from .models import Dataset, Contributor, DatasetVersion
from .serializers import DatasetSerializer
from rest_framework.permissions import IsAuthenticated
from apps.accounts.permissions import CanUploadDatasets
from django.contrib.auth import get_user_model
from apps.sharing.models import DatasetAccessRequest
import uuid
RECEIVED_DOWNLOAD_ACTIONS = ["owner_download", "contributor_download", "dataset_download", "reviewer_download"]


def _my_dataset_ids(user):
    """Owned outright, or co-owned via Contributor(contributor_type=OWNER)."""
    owned = set(Dataset.objects.filter(owner=user, is_active=True).values_list("id", flat=True))
    coowned = set(Contributor.objects.filter(
        user=user, contributor_type=Contributor.ContributorType.OWNER, dataset__is_active=True
    ).values_list("dataset_id", flat=True))
    return owned | coowned


@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def dashboard_stats(request):
    """Top-level numbers: how many datasets they hold, how much attention those
    datasets have received, and — separately — how much downloading THEY have
    personally done (of any dataset, theirs or not)."""
    my_ids = _my_dataset_ids(request.user)
    my_datasets_qs = Dataset.objects.filter(id__in=my_ids)

    totals = my_datasets_qs.aggregate(
        total_views_received=Sum("view_count"), total_downloads_received=Sum("download_count")
    )
    most_viewed = my_datasets_qs.order_by("-view_count").first()

    downloads_i_made = ActivityLog.objects.filter(
        user=request.user, action__in=RECEIVED_DOWNLOAD_ACTIONS
    ).count()

    return Response({
        "total_datasets": my_datasets_qs.count(),
        "total_views_received": totals["total_views_received"] or 0,
        "total_downloads_received": totals["total_downloads_received"] or 0,
        "downloads_i_made": downloads_i_made,
        "most_viewed_dataset": {
            "id": most_viewed.id, "title": most_viewed.title, "view_count": most_viewed.view_count,
        } if most_viewed else None,
    })



UPLOAD_ACTIONS = ["dataset_upload_initiated", "dataset_upload_session_initiated"]
MODIFY_LOG_ACTIONS = ["minor_revision_applied"]
DOWNLOAD_ACTIONS = ["owner_download", "contributor_download", "dataset_download", "reviewer_download", "admin_download"]


def _actor_name(user):
    if not user:
        return "Unknown"
    profile = getattr(user, "profile", None)
    return profile.full_name if profile else user.email


@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def recent_activity(request):
    """Interactions made ON datasets this researcher owns or co-owns.
    Pulled from three tables since they're recorded differently:
      - ActivityLog: upload, minor-revision "modify", download
      - DatasetVersion: major-revision "modify" (these never hit ActivityLog)
      - DatasetAccessRequest: "request" (requester acted for themself, shared_by
        is null) vs "share" (someone with access shared it onward, shared_by is set)
    """
    my_ids = _my_dataset_ids(request.user)
    titles = dict(Dataset.objects.filter(id__in=my_ids).values_list("id", "title"))
    targets = [f"Dataset:{id}" for id in my_ids]

    events = []

    logs = ActivityLog.objects.filter(
        target_object__in=targets,
        action__in=UPLOAD_ACTIONS + MODIFY_LOG_ACTIONS + DOWNLOAD_ACTIONS,
    ).exclude(user=request.user).select_related("user__profile")

    for log in logs:
        if log.action in UPLOAD_ACTIONS:
            action = "upload"
        elif log.action in MODIFY_LOG_ACTIONS:
            action = "modify"
        else:
            action = "download"
        dataset_id = uuid.UUID(log.target_object.split(":", 1)[1])
        events.append({
            "action": action,
            "dataset_id": dataset_id,
            "dataset_title": titles.get(dataset_id, "Unknown dataset"),
            "user": _actor_name(log.user),
            "timestamp": log.timestamp,
        })

    major_versions = DatasetVersion.objects.filter(
        dataset_id__in=my_ids,
    ).exclude(changed_by=request.user).select_related("changed_by__profile")

    for v in major_versions:
        events.append({
            "action": "modify",
            "dataset_id": v.dataset_id,
            "dataset_title": titles.get(v.dataset_id, "Unknown dataset"),
            "user": _actor_name(v.changed_by),
            "timestamp": v.created_at,
        })

    access_events = DatasetAccessRequest.objects.filter(
        dataset_id__in=my_ids,
    ).exclude(requester=request.user).exclude(shared_by=request.user).select_related(
        "requester__profile", "shared_by__profile",
    )

    for ar in access_events:
        actor = ar.shared_by or ar.requester
        events.append({
            "action": "share" if ar.shared_by_id else "request",
            "dataset_id": ar.dataset_id,
            "dataset_title": titles.get(ar.dataset_id, "Unknown dataset"),
            "user": _actor_name(actor) if actor else ar.requester_email,
            "timestamp": ar.created_at,
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return Response(events[:30])

@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def feed(request):
    """Recently approved datasets matching this researcher's declared interests —
    all visibility tiers, since restricted/institutional datasets can still be
    requested; the feed shows what exists, access control happens at request time."""
    profile = request.user.profile
    interest_category_ids = list(profile.interests.values_list("id", flat=True))
    my_ids = _my_dataset_ids(request.user)

    qs = Dataset.objects.filter(
        status=Dataset.Status.APPROVED, is_active=True, is_archived=False,
    ).exclude(id__in=my_ids).exclude(visibility=Dataset.Visibility.PRIVATE)

    if interest_category_ids:
        qs = qs.filter(metadata__category_id__in=interest_category_ids)

    qs = qs.order_by("-created_at")[:20]
    return Response(DatasetSerializer(qs, many=True).data)

@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def my_contributions(request):
    my_ids = _my_dataset_ids(request.user)

    major_edit_ids = set(
        DatasetVersion.objects.filter(changed_by=request.user)
        .values_list("dataset_id", flat=True)
    )

    minor_edit_targets = ActivityLog.objects.filter(
        user=request.user,
        action="minor_revision_applied",
        target_object__startswith="Dataset:",
    ).values_list("target_object", flat=True)
    minor_edit_ids = {uuid.UUID(t.split(":", 1)[1]) for t in minor_edit_targets}

    dataset_ids = (major_edit_ids | minor_edit_ids) - my_ids

    qs = Dataset.objects.filter(id__in=dataset_ids, is_active=True)
    return Response(DatasetSerializer(qs, many=True).data)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_contributor_datasets(request):
    dataset_ids = Contributor.objects.filter(
        user=request.user,
        contributor_type=Contributor.ContributorType.CONTRIBUTOR,
        dataset__is_active=True,
    ).values_list("dataset_id", flat=True).distinct()

    qs = Dataset.objects.filter(
        id__in=dataset_ids,
        is_active=True,
    ).order_by("-created_at")

    return Response(DatasetSerializer(qs, many=True).data)