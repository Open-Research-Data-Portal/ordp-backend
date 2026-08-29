from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Count, F
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Count, F
from apps.accounts.permissions import IsReviewerOrAdmin, IsAdminOnly
from apps.datasets.models import Dataset, PendingContentUpdate
from apps.datasets.serializers import PendingContentUpdateSerializer
from apps.metadata.models import FallbackThumbnail
from apps.notifications.services import notify
from apps.notifications.models import Notification
from django.contrib.auth import get_user_model

from .models import (
    ModerationDecision,
    DatasetReviewerAssignment,
    ThumbnailSuggestion,
    DatasetDeletionRequest,
    DeletionRequestVote,
)
from .serializers import ModerationQueueItemSerializer
from .services import resolve_deletion_request_votes, execute_hard_delete

def _resolve_thumbnail_suggestions(dataset):
    """Called only on approval. Majority-suggested fallback wins; a tie or no
    suggestions at all means we leave whatever attach_metadata auto-assigned —
    'the system decides its own'."""
    tally = (ThumbnailSuggestion.objects.filter(dataset=dataset)
             .values("fallback_thumbnail").annotate(n=Count("id")).order_by("-n"))
    if len(tally) >= 1 and (len(tally) == 1 or tally[0]["n"] > tally[1]["n"]):
        winner = FallbackThumbnail.objects.get(id=tally[0]["fallback_thumbnail"])
        Dataset.objects.filter(id=dataset.id).update(
            thumbnail_key=winner.image_key, thumbnail_source=Dataset.ThumbnailSource.FALLBACK_REVIEWER_SELECTED
        )
        FallbackThumbnail.objects.filter(id=winner.id).update(usage_count=F("usage_count") + 1)


@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def moderation_queue(request):
    profile = getattr(request.user, "profile", None)

    if profile and profile.has_role("admin"):
        # Admins can see all active datasets regardless of status.
        qs = Dataset.objects.filter(
            is_active=True,
        ).distinct()
    else:
        # Reviewers only see pending datasets assigned to them.
        qs = Dataset.objects.filter(
            status=Dataset.Status.PENDING,
            is_active=True,
            reviewer_assignments__reviewer=request.user,
        ).distinct()

    return Response(
        ModerationQueueItemSerializer(
            qs.order_by("created_at"),
            many=True,
        ).data
    )
@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def my_reviews(request):
    decisions = (
        ModerationDecision.objects
        .filter(reviewer=request.user)
        .select_related("dataset")
        .order_by("-decided_at")
    )

    data = [
        {
            "dataset_id": str(decision.dataset.id),
            "dataset_title": decision.dataset.title,
            "decision": decision.decision,
            "reason": decision.reason,
            "dataset_status": decision.dataset.status,
            "decided_at": decision.decided_at,
        }
        for decision in decisions
    ]

    return Response(data)

@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def moderate_dataset(request, dataset_id):
    dataset = get_object_or_404(
        Dataset,
        id=dataset_id,
        status=Dataset.Status.PENDING,
    )

    decision = request.data.get("decision")
    reason = (request.data.get("reason") or "").strip()

    if decision not in ModerationDecision.Decision.values:
        return Response(
            {
                "detail": (
                    "decision must be 'approved', "
                    "'changes_requested', or 'rejected'."
                )
            },
            status=400,
        )

    if (
        decision in (
            ModerationDecision.Decision.REJECTED,
            ModerationDecision.Decision.CHANGES_REQUESTED,
        )
        and not reason
    ):
        return Response(
            {
                "detail": (
                    "A reason is required to reject or "
                    "request changes on a dataset."
                )
            },
            status=400,
        )

    assigned = DatasetReviewerAssignment.objects.filter(
        dataset=dataset,
        reviewer=request.user,
    ).exists()

    if not assigned:
        return Response(
            {"detail": "You are not assigned to review this dataset."},
            status=403,
        )

    if ModerationDecision.objects.filter(
        dataset=dataset,
        reviewer=request.user,
    ).exists():
        return Response(
            {"detail": "You have already submitted a decision for this dataset."},
            status=400,
        )

    ModerationDecision.objects.create(
        dataset=dataset,
        reviewer=request.user,
        decision=decision,
        reason=reason or None,
    )

    approve_votes = ModerationDecision.objects.filter(
        dataset=dataset,
        decision=ModerationDecision.Decision.APPROVED,
    ).count()

    reject_votes = ModerationDecision.objects.filter(
        dataset=dataset,
        decision=ModerationDecision.Decision.REJECTED,
    ).count()

    votes_cast = ModerationDecision.objects.filter(
        dataset=dataset,
    ).count()

    assigned_count = DatasetReviewerAssignment.objects.filter(
        dataset=dataset,
    ).count()

    if assigned_count < 3:
        return Response(
    {"detail": "Your review decision has been submitted successfully."},
    status=200,
)

    if approve_votes >= 2:
        dataset.status = Dataset.Status.PUBLISHED
        dataset.save(update_fields=["status"])

        _resolve_thumbnail_suggestions(dataset)

        notify(
            user=dataset.owner,
            notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'Your dataset "{dataset.title}" has been published.',
            dataset=dataset,
            link_path=f"/datasets/{dataset.id}",
        )

        return Response(
            {
                "status": "approved",
                "approve_votes": approve_votes,
                "reject_votes": reject_votes,
                "votes_cast": votes_cast,
            },
            status=200,
        )

    if ModerationDecision.objects.filter(
        dataset=dataset,
        decision=ModerationDecision.Decision.CHANGES_REQUESTED,
    ).count() >= 2:
        dataset.status = Dataset.Status.CHANGES_REQUESTED
        dataset.save(update_fields=["status"])

        notify(
            user=dataset.owner,
            notification_type=Notification.NotificationType.CHANGES_REQUESTED,
            message=f'Changes were requested on "{dataset.title}".',
            dataset=dataset,
            reason=reason,
            link_path=f"/datasets/{dataset.id}",
        )

        return Response(
    {"detail": "Your review decision has been submitted successfully."},
    status=200,
)

    if ModerationDecision.objects.filter(
        dataset=dataset,
        decision=ModerationDecision.Decision.CHANGES_REQUESTED,
    ).count() >= 2:
        dataset.status = Dataset.Status.CHANGES_REQUESTED
        dataset.save(update_fields=["status"])

        notify(
            user=dataset.owner,
            notification_type=Notification.NotificationType.CHANGES_REQUESTED,
            message=f'Changes were requested on "{dataset.title}".',
            dataset=dataset,
            reason=reason,
            link_path=f"/datasets/{dataset.id}",
        )

        return Response(
    {"detail": "Your review decision has been submitted successfully."},
    status=200,
)

    if reject_votes >= 2:
        dataset.status = Dataset.Status.REJECTED
        dataset.save(update_fields=["status"])

        notify(
            user=dataset.owner,
            notification_type=Notification.NotificationType.DATASET_REJECTED,
            message=f'Your dataset "{dataset.title}" was rejected: {reason}',
            dataset=dataset,
            reason=reason,
            link_path=f"/datasets/{dataset.id}",
        )

        return Response(
            {
                "status": "rejected",
                "approve_votes": approve_votes,
                "reject_votes": reject_votes,
                "votes_cast": votes_cast,
            },
            status=200,
        )

    return Response(
    {"detail": "Your review decision has been submitted successfully."},
    status=200,
)

@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def suggest_thumbnail(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, status=Dataset.Status.PENDING)
    fallback_id = request.data.get("fallback_thumbnail_id")
    fallback = get_object_or_404(FallbackThumbnail, id=fallback_id, category=dataset.metadata.category)
    ThumbnailSuggestion.objects.update_or_create(
        dataset=dataset, reviewer=request.user, defaults={"fallback_thumbnail": fallback}
    )
    return Response({"status": "suggestion recorded"}, status=200)


@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def content_update_queue(request):
    qs = PendingContentUpdate.objects.filter(status="pending").select_related("dataset", "submitted_by")
    return Response(PendingContentUpdateSerializer(qs, many=True).data)



@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def request_dataset_deletion(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    reason = (request.data.get("reason") or "").strip()
    if not reason:
        return Response({"detail": "A reason is required to request deletion."}, status=400)

    deletion_request = DatasetDeletionRequest.objects.create(
        dataset=dataset, dataset_title=dataset.title, requested_by=request.user, reason=reason,
    )
    for reviewer in get_user_model().objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct():
        if reviewer != request.user:
            notify(
                user=reviewer, notification_type=Notification.NotificationType.ACCESS_REQUEST,
                message=f'{request.user.profile.full_name} requested permanent deletion of "{dataset.title}".',
                dataset=dataset, link_path=f"/admin-panel/deletion-requests/{deletion_request.id}",
            )
    return Response({"status": "pending", "request_id": deletion_request.id}, status=201)


@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def vote_on_deletion_request(request, request_id):
    deletion_request = get_object_or_404(DatasetDeletionRequest, id=request_id)
    if deletion_request.status != DatasetDeletionRequest.Status.PENDING:
        return Response({"detail": "This request has already been resolved."}, status=400)

    vote_value = request.data.get("vote")
    if vote_value not in ("approve", "reject"):
        return Response({"detail": "vote must be 'approve' or 'reject'."}, status=400)

    DeletionRequestVote.objects.update_or_create(
        deletion_request=deletion_request, reviewer=request.user, defaults={"vote": vote_value}
    )
    result = resolve_deletion_request_votes(deletion_request)
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAdminOnly])
def deletion_request_queue(request):
    qs = DatasetDeletionRequest.objects.filter(
        status=DatasetDeletionRequest.Status.APPROVED
    ).select_related("dataset", "requested_by")
    return Response([{
        "id": r.id, "dataset_id": r.dataset_id, "dataset_title": r.dataset.title,
        "requested_by": r.requested_by.profile.full_name, "reason": r.reason,
        "resolved_at": r.resolved_at,
    } for r in qs])


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def execute_deletion(request, request_id):
    deletion_request = get_object_or_404(DatasetDeletionRequest, id=request_id, status=DatasetDeletionRequest.Status.APPROVED)
    title = execute_hard_delete(deletion_request, request.user)
    return Response({"status": "executed", "deleted_dataset_title": title})


