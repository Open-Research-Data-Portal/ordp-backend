from .models import Notification

NT = Notification.NotificationType
DASHBOARD_VISIBLE = {NT.NEW_VERSION_AVAILABLE: True}

EMAIL_SUBJECTS = {
    NT.UPLOAD_SUCCESS: "Your dataset upload succeeded",
    NT.UPLOAD_FAILURE: "Your dataset upload failed",
    NT.NEW_VERSION_AVAILABLE: "A dataset you downloaded has a new version",
    NT.DATASET_APPROVED: "Your dataset has been approved",
    NT.DATASET_REJECTED: "Your dataset submission was rejected",
    NT.REVISION_PROPOSED: "A revision has been proposed for your dataset",
    NT.REVISION_REJECTED: "Your proposed revision was declined",
    NT.CONTENT_UPDATE_PENDING: "A significant content change awaits your review",
    NT.RESEARCHER_REQUEST: "New researcher access request",
    NT.CONTRIBUTOR_INVITATION: "You've been added as a contributor",
    NT.ACCESS_REQUEST: "New access request for your restricted dataset",
}


def is_dashboard_visible(notification_type):
    return DASHBOARD_VISIBLE.get(notification_type, False)


def email_subject(notification_type):
    return EMAIL_SUBJECTS.get(notification_type, "ORDP Notification")