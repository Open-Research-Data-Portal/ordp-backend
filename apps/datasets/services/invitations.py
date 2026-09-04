from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from ..models import Contributor, DatasetInvitation, PermissionLevel
from apps.sharing.models import SharePermission

User = get_user_model()
INVITATION_EXPIRY_DAYS = 14


def create_invitation(dataset, invited_by, email, role, permission=PermissionLevel.VIEW):
    invitation = DatasetInvitation.objects.create(
        dataset=dataset, invited_email=email.lower().strip(), role=role,
        permission=permission,
        invited_by=invited_by, expires_at=timezone.now() + timedelta(days=INVITATION_EXPIRY_DAYS),
    )
    _send_invitation_email(invitation)
    return invitation


def _send_invitation_email(invitation):
    link = f"{settings.FRONTEND_URL}/dataset-invitations/{invitation.token}"
    role_label = "co-author" if invitation.role == DatasetInvitation.Role.CO_AUTHOR else "contributor"
    send_mail(
        subject=f'You\'ve been invited as a {role_label} on "{invitation.dataset.title}"',
        message=(
            f'{invitation.invited_by.profile.full_name} invited you to be a {role_label} '
            f'on "{invitation.dataset.title}" on ORDP.\n\n'
            f"View and accept the invitation here: {link}\n\n"
            f"This link expires on {invitation.expires_at.strftime('%Y-%m-%d')}."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[invitation.invited_email],
    )


def accept_invitation(token, user):
    try:
        invitation = DatasetInvitation.objects.select_related("dataset").get(token=token)
    except DatasetInvitation.DoesNotExist:
        raise ValueError("This invitation link is invalid.")

    if invitation.status == DatasetInvitation.Status.ACCEPTED:
        raise ValueError("This invitation has already been accepted.")
    if invitation.status == DatasetInvitation.Status.REVOKED:
        raise ValueError("This invitation has been revoked.")
    if invitation.expires_at <= timezone.now():
        invitation.status = DatasetInvitation.Status.EXPIRED
        invitation.save(update_fields=["status"])
        raise ValueError("This invitation link has expired.")
    if user.email.lower() != invitation.invited_email.lower():
        raise ValueError("This invitation was sent to a different email address.")

    contributor_type = (
        Contributor.ContributorType.CO_AUTHOR if invitation.role == DatasetInvitation.Role.CO_AUTHOR
        else Contributor.ContributorType.CONTRIBUTOR
    )
    contributor, _ = Contributor.objects.update_or_create(
        dataset=invitation.dataset, user=user,
        defaults={
            "name": user.profile.full_name, "invited_email": "", "contributor_type": contributor_type,
            "order": invitation.dataset.contributors.count() + 1,
        },
    )
    SharePermission.objects.get_or_create(
        dataset=invitation.dataset, shared_with_user=user, defaults={"access_type": "download"},
    )
    invitation.status = DatasetInvitation.Status.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at"])
    return contributor