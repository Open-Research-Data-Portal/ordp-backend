from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import Notification
from .config import DASHBOARD_VISIBLE
from .serializers import NotificationSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bell_notifications(request):
    visible_types = [t for t, show in DASHBOARD_VISIBLE.items() if show]
    qs = Notification.objects.filter(
        user=request.user, notification_type__in=visible_types, is_deleted=False,
    )
    return Response({
        "unread_count": qs.filter(is_read=False).count(),
        "notifications": NotificationSerializer(qs[:20], many=True).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, notification_id):
    Notification.objects.filter(id=notification_id, user=request.user).update(is_read=True)
    return Response(status=204)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_notification(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    notification.is_deleted = True
    notification.save(update_fields=["is_deleted"])
    return Response(status=204)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_history(request):
    qs = Notification.objects.filter(user=request.user, is_deleted=False)
    return Response(NotificationSerializer(qs[:100], many=True).data)