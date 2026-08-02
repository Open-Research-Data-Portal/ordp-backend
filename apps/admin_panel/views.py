from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import IsCheckerOrAdmin
from apps.datasets.models import Dataset
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import ModerationDecision
from .serializers import ModerationQueueItemSerializer

from apps.datasets.models import PendingContentUpdate
from apps.datasets.serializers import PendingContentUpdateSerializer
from apps.datasets.services.revisions import decide_pending_content_update

from django.utils import timezone
from apps.accounts.permissions import IsAdminOnly
from apps.accounts.models import ResearcherRequest, UserProfile


@api_view(["GET"])
@permission_classes([IsCheckerOrAdmin])
def moderation_queue(request):
    qs = Dataset.objects.filter(status=Dataset.Status.PENDING, is_active=True)
    if request.user.profile.role != "admin":
        qs = qs.filter(assigned_reviewer=request.user)
    qs = qs.order_by("created_at")
    return Response(ModerationQueueItemSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsCheckerOrAdmin])
def moderate_dataset(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id)
    decision = request.data.get("decision")
    reason = (request.data.get("reason") or "").strip()

    if decision == ModerationDecision.Decision.REJECTED and not reason:
        return Response({"detail": "A reason is required to reject a dataset."}, status=400)

    ModerationDecision.objects.create(dataset=dataset, reviewer=request.user, decision=decision, reason=reason or None)

    if decision == ModerationDecision.Decision.APPROVED:
        dataset.status = Dataset.Status.APPROVED
        dataset.save(update_fields=["status"])
        notify(
            user=dataset.owner, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'Your dataset "{dataset.title}" has been approved.', dataset=dataset,
            link_path=f"/datasets/{dataset.id}",
        )
    else:
        dataset.status = Dataset.Status.REJECTED
        dataset.save(update_fields=["status"])
        notify(
            user=dataset.owner, notification_type=Notification.NotificationType.DATASET_REJECTED,
            message=f'Your dataset "{dataset.title}" was rejected: {reason}', dataset=dataset, reason=reason,
            link_path=f"/datasets/{dataset.id}",
        )

    return Response({"status": decision}, status=200)



@api_view(["GET"])
@permission_classes([IsCheckerOrAdmin])
def content_update_queue(request):
    qs = PendingContentUpdate.objects.filter(status="pending").select_related("dataset", "submitted_by")
    return Response(PendingContentUpdateSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsCheckerOrAdmin])
def decide_content_update(request, update_id):
    update = get_object_or_404(PendingContentUpdate, id=update_id)
    decision = request.data.get("decision")
    reason = (request.data.get("reason") or "").strip()
    if decision == "reject" and not reason:
        return Response({"detail": "A reason is required to reject a content update."}, status=400)
    decide_pending_content_update(update, decision, request.user, reason)
    return Response({"status": update.status})

@api_view(["GET"]) 
@permission_classes([IsAdminOnly])
def researcher_request_queue(request):
    qs = ResearcherRequest.objects.filter(
        status=ResearcherRequest.Status.PENDING
    ).select_related("user", "user__profile")
    return Response([{
        "id": r.id,
        "email": r.user.email,
        "full_name": r.user.profile.full_name,
        "academia": r.user.profile.academia,
        "department": str(r.user.profile.department) if r.user.profile.department else None,
        "submitted_at": r.submitted_at,
    } for r in qs])


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def decide_researcher_request(request, request_id):
    req = get_object_or_404(ResearcherRequest, id=request_id)
    decision = request.data.get("decision")
    reason = (request.data.get("reason") or "").strip()

    if decision == "reject" and not reason:
        return Response({"detail": "A reason is required to reject a researcher request."}, status=400)

    req.decided_by = request.user
    req.decided_at = timezone.now()

    if decision == "approve":
        req.status = ResearcherRequest.Status.APPROVED
        req.user.profile.role = UserProfile.Role.RESEARCHER
        req.user.profile.save(update_fields=["role"])
        notify(
            user=req.user, notification_type=Notification.NotificationType.RESEARCHER_APPROVED,
            message="Your researcher access request has been approved. You can now upload datasets.",
        )
    else:
        req.status = ResearcherRequest.Status.REJECTED
        req.reason = reason
        notify(
            user=req.user, notification_type=Notification.NotificationType.RESEARCHER_REJECTED,
            message=f"Your researcher access request was declined: {reason}", reason=reason,
        )

    req.save()
    return Response({"status": req.status})

