from django.db.models import Q, Sum, F
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.accounts.models import ActivityLog
from .models import Dataset, Contributor, DatasetRevision
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
        status=Dataset.Status.APPROVED, is_active=True,
    ).exclude(id__in=my_ids)

    if interest_category_ids:
        qs = qs.filter(metadata__category_id__in=interest_category_ids)

    qs = qs.order_by("-created_at")[:20]
    return Response(DatasetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([CanUploadDatasets])
def my_contributions(request):
    """'Uploads' tab per your spec — datasets this researcher has MODIFIED that
    belong to someone else. Distinct from 'mine' (owned/co-owned) and distinct
    from 'invited as contributor' — this is specifically their edit history on
    other people's datasets, via applied revisions."""
    dataset_ids = (
        DatasetRevision.objects.filter(
            submitted_by=request.user, status=DatasetRevision.Status.APPROVED,
        )
        .exclude(dataset__owner=request.user)
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