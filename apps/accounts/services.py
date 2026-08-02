from django.utils import timezone
from .models import ResearcherRequest, UserProfile
from apps.notifications.services import notify
from apps.notifications.models import Notification


def decide_researcher_request(req, decision, decided_by, reason=""):
    reason = (reason or "").strip()
    if decision == "reject" and not reason:
        raise ValueError("A reason is required to reject a researcher request.")

    req.decided_by = decided_by
    req.decided_at = timezone.now()

    if decision == "approve":
        req.status = ResearcherRequest.Status.APPROVED
        req.user.profile.role = UserProfile.Role.RESEARCHER
        req.user.profile.save(update_fields=["role"])
        notify(user=req.user, notification_type=Notification.NotificationType.RESEARCHER_APPROVED,
               message="Your researcher access request has been approved. You can now upload datasets.")
    else:
        req.status = ResearcherRequest.Status.REJECTED
        req.reason = reason
        notify(user=req.user, notification_type=Notification.NotificationType.RESEARCHER_REJECTED,
               message=f"Your researcher access request was declined: {reason}", reason=reason)

    req.save()
    return req