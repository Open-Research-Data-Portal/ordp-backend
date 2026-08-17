from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .models import Notification
from .config import DASHBOARD_VISIBLE
from .serializers import NotificationSerializer


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bell_notifications(request):
    visible_types = [t for t, show in DASHBOARD_VISIBLE.items() if show]
    qs = Notification.objects.filter(user=request.user, notification_type__in=visible_types)
    return Response({
        "unread_count": qs.filter(is_read=False).count(),
        "notifications": NotificationSerializer(qs[:20], many=True).data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def mark_notification_read(request, notification_id):
    Notification.objects.filter(id=notification_id, user=request.user).update(is_read=True)
    return Response(status=204)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notification_history(request):
    qs = Notification.objects.filter(user=request.user)
    return Response(NotificationSerializer(qs[:100], many=True).data)