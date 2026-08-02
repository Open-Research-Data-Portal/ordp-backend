from django.conf import settings
from django.core.mail import send_mail
from .models import Notification
from .config import email_subject


def notify(user, notification_type, message, dataset=None, reason=None, link_path=""):
    """Single entry point for every notification in the system."""
    notif = Notification.objects.create(
        user=user, dataset=dataset, notification_type=notification_type,
        message=message, reason=reason, link_path=link_path,
    )
    link = f"{settings.FRONTEND_URL}{link_path}" if link_path else settings.FRONTEND_URL
    body = f"{message}\n\n{('View: ' + link) if link_path else ''}".strip()
    try:
        send_mail(
            subject=email_subject(notification_type), message=body,
            from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[user.email],
        )
        notif.email_sent = True
        notif.save(update_fields=["email_sent"])
    except Exception:
        pass  
    return notif