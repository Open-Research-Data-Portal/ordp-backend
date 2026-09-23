from datetime import timezone as datetime_timezone

from django.db.models import Q, Sum, F
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.accounts.models import ActivityLog
from .models import Dataset, Contributor, DatasetRevision, DatasetVersion
from .serializers import DatasetSerializer
from rest_framework.permissions import IsAuthenticated
from apps.accounts.permissions import CanUploadDatasets
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


@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def recent_activity(request):
    """Interactions made ON datasets this researcher owns or co-owns — someone
    else downloading their work, a revision proposed against it, etc."""
    my_ids = _my_dataset_ids(request.user)
    targets = [f"Dataset:{id}" for id in my_ids]
    logs = ActivityLog.objects.filter(target_object__in=targets).exclude(user=request.user).order_by("-timestamp")[:30]
    return Response([{
        "user": log.user.profile.full_name if log.user else "Unknown",
        "action": log.action,
        "target_object": log.target_object,
        "timestamp": log.timestamp,
    } for log in logs])

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
    """Datasets this researcher has MODIFIED that belong to someone else —
    i.e. they got their change approved through the outsider revision flow
    (request-revision-permission -> propose-revision -> route_change), not
    because they were invited as a co-author. Every approved change (owner
    edit, co-author edit, or outsider revision) lands in DatasetVersion via
    route_change/_apply_pending_content_update, so that's the source of
    truth — not the legacy DatasetRevision model, which nothing writes to
    anymore. We also exclude datasets where the user is currently an
    invited co-author: those edits are 'their own dataset' work, not an
    uninvited contribution."""
    coauthor_dataset_ids = Contributor.objects.filter(
        user=request.user, contributor_type=Contributor.ContributorType.CO_AUTHOR,
    ).values_list("dataset_id", flat=True)

    dataset_ids = (
        DatasetVersion.objects.filter(changed_by=request.user)
        .exclude(dataset__owner=request.user)
        .exclude(dataset_id__in=coauthor_dataset_ids)
        .values_list("dataset_id", flat=True)
        .distinct()
    )
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


@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def recent_activity(request):
    my_ids = _my_dataset_ids(request.user)
    dataset_map = {
        str(dataset_id): dataset
        for dataset_id, dataset in Dataset.objects.filter(id__in=my_ids).in_bulk().items()
    }
    targets = [f"Dataset:{dataset_id}" for dataset_id in my_ids]

    def user_label(user):
        if not user:
            return "Unknown"
        profile = getattr(user, "profile", None)
        return getattr(profile, "full_name", None) or user.get_full_name() or user.email or user.username

    def dataset_id_from_target(target_object):
        prefix = "Dataset:"
        target = str(target_object or "")
        return target[len(prefix):] if target.startswith(prefix) else None

    events = []

    logs = (
        ActivityLog.objects
        .filter(target_object__in=targets)
        .exclude(user=request.user)
        .select_related("user", "user__profile")
        .order_by("-timestamp")[:30]
    )
    for log in logs:
        dataset_id = dataset_id_from_target(log.target_object)
        dataset = dataset_map.get(dataset_id) if dataset_id else None
        events.append({
            "user": user_label(log.user),
            "action": log.action,
            "dataset_id": dataset_id,
            "dataset_title": dataset.title if dataset else log.target_object,
            "timestamp": log.timestamp,
        })

    versions = (
        DatasetVersion.objects
        .filter(dataset_id__in=my_ids)
        .exclude(changed_by=request.user)
        .select_related("dataset", "changed_by", "changed_by__profile")
        .order_by("-created_at")[:30]
    )
    for version in versions:
        events.append({
            "user": user_label(version.changed_by),
            "action": "modify",
            "dataset_id": str(version.dataset_id),
            "dataset_title": version.dataset.title,
            "timestamp": version.created_at,
        })

    from apps.sharing.models import DatasetAccessRequest
    access_requests = (
        DatasetAccessRequest.objects
        .filter(dataset_id__in=my_ids)
        .exclude(requester=request.user)
        .select_related("dataset", "requester", "requester__profile", "shared_by", "shared_by__profile")
        .order_by("-created_at")[:30]
    )
    for access_request in access_requests:
        actor = access_request.shared_by or access_request.requester
        events.append({
            "user": user_label(actor) if actor else access_request.requester_email,
            "action": "share" if access_request.shared_by_id else "request",
            "dataset_id": str(access_request.dataset_id),
            "dataset_title": access_request.dataset.title,
            "timestamp": access_request.created_at,
        })

    fallback_time = timezone.datetime.min.replace(tzinfo=datetime_timezone.utc)
    events.sort(key=lambda event: event["timestamp"] or fallback_time, reverse=True)
    return Response(events[:30])
