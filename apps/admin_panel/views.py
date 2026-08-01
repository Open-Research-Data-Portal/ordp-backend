from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import IsCheckerOrAdmin
from apps.datasets.models import Dataset
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import ModerationDecision
from .serializers import ModerationQueueItemSerializer


@api_view(["GET"])
@permission_classes([IsCheckerOrAdmin])
def moderation_queue(request):
    qs = Dataset.objects.filter(status=Dataset.Status.PENDING, is_active=True).order_by("created_at")
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