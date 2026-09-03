from django.db.models.aggregates import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model
from apps.accounts.views import get_client_ip
from apps.datasets.models import Dataset, PendingContentUpdate
from apps.sharing.models import DatasetAccessRequest, AccessRequestVote
from .models import ModerationDecision, DatasetDeletionRequest, DeletionRequestVote
from apps.datasets.models import DatasetFile
import csv
import logging
from io import BytesIO
from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings
from apps.accounts.models import (
    UserProfile,
    College,
    CenterOfExcellence,
    PasswordResetToken,
    ActivityLog,
    UserRole
)
from django.http import HttpResponse
from reportlab.lib import colors # type: ignore
from reportlab.lib.pagesizes import landscape, letter # type: ignore
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle # type: ignore
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import IsAdminOnly, IsReviewerOrAdmin
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from apps.metadata.models import Category
from apps.accounts.views import get_client_ip
from apps.accounts.utils import generate_username
User = get_user_model()
RECEIVED_DOWNLOAD_ACTIONS = ["owner_download", "contributor_download", "dataset_download", "reviewer_download"]


@api_view(["GET"])
@permission_classes([IsAdminOnly])
def admin_cards(request):
    storage_used = DatasetFile.objects.aggregate(total=Sum("file_size"))["total"] or 0
    last_24h = timezone.now() - timedelta(hours=24)

    return Response({
        "total_users": User.objects.count(),
        "total_datasets": Dataset.objects.filter(is_active=True).count(),
        "storage_used_bytes": storage_used,
        "recent_activity_count_24h": ActivityLog.objects.filter(timestamp__gte=last_24h).count(),
    })

@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def reviewer_overview(request):
    """Everything currently waiting on this reviewer, in one place. Admins see the
    same shape but scoped to what THEY personally haven't acted on yet — not the
    whole platform, so the number is actually actionable rather than overwhelming."""
    user = request.user

    assigned_pending = Dataset.objects.filter(
        status=Dataset.Status.PENDING, is_active=True, assigned_reviewer=user
    ).count()

    content_updates_pending = PendingContentUpdate.objects.filter(status="pending").count()

    voted_access_ids = AccessRequestVote.objects.filter(reviewer=user).values_list("access_request_id", flat=True)
    access_requests_pending = DatasetAccessRequest.objects.filter(
        status=DatasetAccessRequest.Status.PENDING
    ).exclude(id__in=voted_access_ids).count()

    voted_deletion_ids = DeletionRequestVote.objects.filter(reviewer=user).values_list("deletion_request_id", flat=True)
    deletion_requests_pending = DatasetDeletionRequest.objects.filter(
        status=DatasetDeletionRequest.Status.PENDING
    ).exclude(id__in=voted_deletion_ids).count()

    return Response({
        "assigned_datasets_pending": assigned_pending,
        "content_updates_pending": content_updates_pending,
        "access_requests_awaiting_my_vote": access_requests_pending,
        "deletion_requests_awaiting_my_vote": deletion_requests_pending,
    })


@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def reviewer_metrics(request):
    """This reviewer's own track record — how much they've reviewed, and how
    fast, so they (and an admin looking at the team) can see participation."""
    user = request.user
    decisions = ModerationDecision.objects.filter(reviewer=user)
    thirty_days_ago = timezone.now() - timedelta(days=30)

    return Response({
        "total_reviewed": decisions.count(),
        "total_approved": decisions.filter(decision=ModerationDecision.Decision.APPROVED).count(),
        "total_rejected": decisions.filter(decision=ModerationDecision.Decision.REJECTED).count(),
        "reviewed_last_30_days": decisions.filter(decided_at__gte=thirty_days_ago).count(),
    })


@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def reviewer_guidelines(request):
    """Static reference info reviewers need while making a decision — the actual
    thresholds the system uses, so 'why did this need committee review' has an
    answer without digging through settings.py."""
    from django.conf import settings
    from apps.sharing.services import MIN_REVIEWER_QUORUM as SHARING_QUORUM
    from apps.admin_panel.services import MIN_REVIEWER_QUORUM as DELETION_QUORUM

    return Response({
        "moderation_guidelines": [
            "Confirm the file(s) uploaded match the declared file type — the system already "
            "blocks an obvious mismatch (e.g. an image declared as CSV), but review for subtler cases.",
            "A rejection requires a clear, specific reason — the requester needs to know what to fix.",
            "For restricted-visibility datasets, confirm the justification for the visibility "
            "tier is reasonable given the data's sensitivity.",
        ],
        "content_update_bump_threshold_pct": settings.VERSION_BUMP_THRESHOLD_PCT,
        "sharing_committee_quorum": SHARING_QUORUM,
        "deletion_committee_quorum": DELETION_QUORUM,
    })


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_create_user(request):
    email = request.data.get("email", "").strip().lower()
    full_name = request.data.get("full_name", "").strip()
    role = request.data.get("role", UserRole.RoleChoice.PUBLIC)

    if not email or not full_name:
        return Response(
            {"detail": "email and full_name are required."},
            status=400,
        )


    allowed_domains = ("@aastu.edu.et", "@aastustudent.edu.et")
    if not email.endswith(allowed_domains):
        return Response(
            {"detail": "Only AASTU institutional emails are allowed."},
            status=400,
        )

    if User.objects.filter(email=email).exists():
        return Response(
            {"detail": "A user with this email already exists."},
            status=400,
        )

    if role not in UserRole.RoleChoice.values:
        return Response(
            {"detail": "Invalid role."},
            status=400,
        )

    username = generate_username(full_name)

    user = User.objects.create(
        username=username,
        email=email,
        is_active=True,
    )

    user.set_unusable_password()
    user.save()

    profile = user.profile
    profile.full_name = full_name
    profile.must_change_password = True
    profile.save(update_fields=["full_name", "must_change_password"])

    UserRole.objects.get_or_create(
        profile=profile,
        role=role,
    )

    uid = urlsafe_base64_encode(force_bytes(user.pk))

    reset_token = PasswordResetToken.objects.create(
        user=user,
        expires_at=timezone.now() + timedelta(hours=24),
    )

    reset_link = (
        f"{settings.FRONTEND_URL}/reset-password"
        f"?token={reset_token.token}"
    )


    email_sent = True
    try:
        send_mail(
            subject="Your ORDP account has been created",
            message=f"An admin created an account for you on ORDP. Set your password here: {reset_link}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )
    except Exception:
        email_sent = False
        logging.getLogger(__name__).exception(
            "Failed to send account-creation email to %s", email
        )

    return Response(
        {
            "status": "created",
            "user_id": user.id,
            "email_sent": email_sent,
        },
        status=201,
    )


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_colleges(request):
    if request.method == "GET":
        colleges = College.objects.all().order_by("name")

        return Response([
            {
                "id": college.id,
                "name": college.name,
            }
            for college in colleges
        ])

    name = request.data.get("name", "").strip()

    if not name:
        return Response(
            {"detail": "College name is required."},
            status=400,
        )

    if College.objects.filter(name__iexact=name).exists():
        return Response(
            {"detail": "A college with this name already exists."},
            status=400,
        )

    college = College.objects.create(name=name)

    return Response(
        {
            "id": college.id,
            "name": college.name,
        },
        status=201,
    )

@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_centers_of_excellence(request):
    if request.method == "GET":
        centers = CenterOfExcellence.objects.all().order_by("name")

        return Response([
            {
                "id": center.id,
                "name": center.name,
            }
            for center in centers
        ])

    name = request.data.get("name", "").strip()

    if not name:
        return Response(
            {"detail": "Center of Excellence name is required."},
            status=400,
        )

    if CenterOfExcellence.objects.filter(name__iexact=name).exists():
        return Response(
            {"detail": "A Center of Excellence with this name already exists."},
            status=400,
        )

    center = CenterOfExcellence.objects.create(name=name)

    return Response(
        {
            "id": center.id,
            "name": center.name,
        },
        status=201,
    )
@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_college(request, college_id):
    college = get_object_or_404(College, id=college_id)
    affected_users = UserProfile.objects.filter(college=college).count()
    name = college.name
    college.delete()

    return Response(
        {
            "detail": f"College '{name}' deleted.",
            "affected_users": affected_users,
        },
        status=200,
    )


@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_center_of_excellence(request, center_id):
    center = get_object_or_404(CenterOfExcellence, id=center_id)
    affected_users = UserProfile.objects.filter(center_of_excellence=center).count()
    name = center.name
    center.delete()

    return Response(
        {
            "detail": f"Center of Excellence '{name}' deleted.",
            "affected_users": affected_users,
        },
        status=200,
    )
def _daily_counts(queryset, date_field, days=30):
    cutoff = (timezone.now() - timedelta(days=days)).date()
    grouped = (
        queryset.filter(**{f"{date_field}__date__gte": cutoff})
        .annotate(day=TruncDate(date_field))
        .values("day").annotate(count=Count("id")).order_by("day")
    )
    counts_by_day = {row["day"]: row["count"] for row in grouped}

    today = timezone.now().date()
    return [
        {"date": (cutoff + timedelta(days=i)).isoformat(),
         "count": counts_by_day.get(cutoff + timedelta(days=i), 0)}
        for i in range((today - cutoff).days + 1)
    ]



@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_grant_role(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    role = request.data.get("role")

    if role not in UserRole.RoleChoice.values:
        return Response({"detail": "Invalid role."}, status=400)

    UserRole.objects.get_or_create(
        profile=target_user.profile,
        role=role,
    )

    if role == UserRole.RoleChoice.REVIEWER:
        from apps.datasets.services.retry_assignment import retry_pending_assignments
        retry_pending_assignments()

    return Response({
        "status": "granted",
        "roles": list(
            target_user.profile.roles.values_list("role", flat=True)
        ),
    })


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_revoke_role(request, user_id):
    target_user = get_object_or_404(User, id=user_id) # type: ignore
    role = request.data.get("role")
    from apps.accounts.models import UserRole
    UserRole.objects.filter(profile=target_user.profile, role=role).delete()
    return Response({"status": "revoked", "roles": list(target_user.profile.roles.values_list("role", flat=True))})


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_deactivate_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    if target_user.id == request.user.id:
        return Response({"detail": "You can't deactivate your own account."}, status=400)
    target_user.is_active = False
    target_user.save(update_fields=["is_active"])
    return Response({"status": "deactivated"})


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_reactivate_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id) # type: ignore
    target_user.is_active = True
    target_user.save(update_fields=["is_active"])
    return Response({"status": "reactivated"})


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_broadcast_notification(request):
    from apps.notifications.services import broadcast_system_notification
    message = request.data.get("message")
    link_path = request.data.get("link_path")
    
    if not message:
        return Response({"detail": "Message is required."}, status=400)
    
    broadcast_system_notification(message, link_path)
    return Response({"status": "broadcasted"})

@api_view(["GET"])
@permission_classes([IsAdminOnly])
def admin_graphs(request):
    """Daily uploads, downloads, and views for the past 30 days."""
    uploads = _daily_counts(DatasetFile.objects.all(), "uploaded_at")
    downloads = _daily_counts(ActivityLog.objects.filter(action__in=RECEIVED_DOWNLOAD_ACTIONS), "timestamp")
    views = _daily_counts(ActivityLog.objects.filter(action="dataset_view"), "timestamp")

    return Response({"uploads": uploads, "downloads": downloads, "views": views})


def _filtered_audit_qs(request):
    qs = ActivityLog.objects.select_related("user", "user__profile").all()
    user_id = request.query_params.get("user_id")
    action = request.query_params.get("action")
    date_from = request.query_params.get("date_from")
    date_to = request.query_params.get("date_to")

    if user_id:
        qs = qs.filter(user_id=user_id)
    if action:
        qs = qs.filter(action=action)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)
    return qs


@api_view(["GET"])
@permission_classes([IsAdminOnly])
def audit_log(request):
    """Filterable, capped at 500 most recent matching rows — this is a browse view,
    not a bulk-data endpoint. Use the export endpoints for the full matching set."""
    qs = _filtered_audit_qs(request).order_by("-timestamp")[:500]
    return Response([{
        "id": log.id,
        "user": log.user.profile.full_name if log.user else "Deleted user",
        "action": log.action,
        "target_object": log.target_object,
        "ip_address": log.ip_address,
        "timestamp": log.timestamp,
    } for log in qs])

@api_view(["GET"])
@permission_classes([IsAdminOnly])
def audit_log_distribution(request):
    qs = _filtered_audit_qs(request)
    distribution = qs.values("action").annotate(count=Count("id")).order_by("-count")
    return Response(list(distribution))

@api_view(["GET"])
@permission_classes([IsAdminOnly])
def audit_log_summary(request):
    from apps.datasets.models import PendingContentUpdate
    from apps.sharing.models import DatasetAccessRequest
    from .models import DatasetDeletionRequest

    thirty_days_ago = timezone.now() - timedelta(days=30)
    return Response({
        "total_logs": ActivityLog.objects.count(),
        "total_active_users": User.objects.filter(is_active=True).count(),
        "active_users_last_30_days": User.objects.filter(
            activity_logs__timestamp__gte=thirty_days_ago
        ).distinct().count(),
        "pending_reviews": {
            "dataset_moderation": Dataset.objects.filter(status=Dataset.Status.PENDING, is_active=True).count(),
            "content_updates": PendingContentUpdate.objects.filter(status="pending").count(),
            "access_requests": DatasetAccessRequest.objects.filter(status=DatasetAccessRequest.Status.PENDING).count(),
            "deletion_requests": DatasetDeletionRequest.objects.filter(status=DatasetDeletionRequest.Status.PENDING).count(),
        },
    })


@api_view(["GET"])
@permission_classes([IsAdminOnly])
def audit_log_export(request):
    export_format = request.query_params.get("export_format", "csv").lower()
    qs = _filtered_audit_qs(request).order_by("-timestamp")

    rows = [["User", "Action", "Target", "IP Address", "Timestamp"]] + [
        [
            log.user.profile.full_name if log.user else "Deleted user",
            log.action, log.target_object, log.ip_address, log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        ] for log in qs
    ]

    if export_format == "pdf":
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
        table = Table(rows, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ]))
        doc.build([table])
        buffer.seek(0)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="audit_log.pdf"'
        return response

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_log.csv"'
    writer = csv.writer(response)
    writer.writerows(rows)
    return response

@api_view(["GET"])
@permission_classes([IsAdminOnly])
def list_users(request):
    qs = User.objects.select_related("profile").prefetch_related("profile__roles").all()
    role_filter = request.query_params.get("role")
    search = request.query_params.get("search")
    if role_filter:
        qs = qs.filter(profile__roles__role=role_filter).distinct()
    if search:
        qs = qs.filter(Q(email__icontains=search) | Q(profile__full_name__icontains=search))

    return Response([{
        "id": u.id, "email": u.email, "full_name": getattr(u.profile, "full_name", ""),
        "roles": list(u.profile.roles.values_list("role", flat=True)) if hasattr(u, "profile") else [],
        "is_active": u.is_active, "date_joined": u.date_joined,
    } for u in qs])


# @api_view(["GET"])
# @permission_classes([IsAdminOnly])
# def pending_categories(request):
#     from apps.metadata.models import Category
#     qs = Category.objects.filter(status=Category.Status.PENDING).select_related("suggested_by__profile")
#     return Response([{
#         "id": c.id, "name": c.name,
#         "suggested_by": c.suggested_by.profile.full_name if c.suggested_by else None,
#     } for c in qs])

@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_create_category(request):
    name = (request.data.get("name") or "").strip()
    description = (request.data.get("description") or "").strip()

    if not name:
        return Response(
            {"detail": "name is required."},
            status=400,
        )

    if Category.objects.filter(name__iexact=name).exists():
        return Response(
            {"detail": "A category with this name already exists."},
            status=400,
        )

    category = Category.objects.create(
        name=name,
        description=description,
        status=Category.Status.APPROVED,
        origin=Category.Origin.STANDARD,
        suggested_by=None,
    )

    ActivityLog.log(
        user=request.user,
        action="category_created",
        target_object=str(category.id),
        ip_address=get_client_ip(request),
        extra={
            "category_name": category.name,
            "source": "admin",
        },
    )

    return Response(
        {
            "id": category.id,
            "name": category.name,
            "description": category.description,
            "status": category.status,
        },
        status=201,
    )

# @api_view(["POST"])
# @permission_classes([IsAdminOnly])
# def decide_pending_category(request, category_id):
#     from apps.metadata.models import Category
#     category = get_object_or_404(Category, id=category_id, status=Category.Status.PENDING)
#     decision = request.data.get("decision")
#     if decision == "approve":
#         category.status = Category.Status.APPROVED
#     elif decision == "reject":
#         category.status = Category.Status.REJECTED
#     else:
#         return Response({"detail": "decision must be 'approve' or 'reject'."}, status=400)
#     category.save(update_fields=["status"])
#     return Response({"status": category.status})




@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_revoke_share_permission(request, permission_id):
    from apps.sharing.models import SharePermission
    from apps.sharing.services import revoke_share_permission
    permission = get_object_or_404(SharePermission, id=permission_id)
    revoke_share_permission(permission, request.user)
    return Response({"status": "revoked"})



# @api_view(["GET"])
# @permission_classes([IsAdminOnly])
# def pending_languages(request):
#     from apps.metadata.models import Language
#     qs = Language.objects.filter(status=Language.Status.PENDING).select_related("suggested_by__profile")
#     return Response([{
#         "id": l.id, "name": l.name,
#         "suggested_by": l.suggested_by.profile.full_name if l.suggested_by else None,
#     } for l in qs])


# @api_view(["POST"])
# @permission_classes([IsAdminOnly])
# def decide_pending_language(request, language_id):
#     from apps.metadata.models import Language
#     language = get_object_or_404(Language, id=language_id, status=Language.Status.PENDING)
#     decision = request.data.get("decision")
#     if decision == "approve":
#         language.status = Language.Status.APPROVED
#     elif decision == "reject":
#         language.status = Language.Status.REJECTED
#     else:
#         return Response({"detail": "decision must be 'approve' or 'reject'."}, status=400)
#     language.save(update_fields=["status"])
#     return Response({"status": language.status})





@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def revision_request_queue(request):
    from apps.datasets.models import RevisionRequest
    qs = RevisionRequest.objects.filter(status="pending").select_related("dataset", "requester__profile")
    return Response([{
        "id": r.id, "dataset_id": r.dataset_id, "dataset_title": r.dataset.title,
        "requester": r.requester.profile.full_name, "reason": r.reason, "created_at": r.created_at,
    } for r in qs])


@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def vote_on_revision_request(request, request_id):
    from apps.datasets.models import RevisionRequest, RevisionRequestVote
    from apps.datasets.services.revisions import resolve_revision_request_votes
    revision_request = get_object_or_404(RevisionRequest, id=request_id)
    if revision_request.status != RevisionRequest.Status.PENDING:
        return Response({"detail": "This request has already been resolved."}, status=400)
    vote_value = request.data.get("vote")
    if vote_value not in ("approve", "reject"):
        return Response({"detail": "vote must be 'approve' or 'reject'."}, status=400)
    RevisionRequestVote.objects.update_or_create(
        revision_request=revision_request, reviewer=request.user, defaults={"vote": vote_value}
    )
    return Response(resolve_revision_request_votes(revision_request))


@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def vote_on_content_update(request, update_id):
    from apps.datasets.models import PendingContentUpdate, PendingContentUpdateVote
    from apps.datasets.services.revisions import resolve_content_update_votes
    update = get_object_or_404(PendingContentUpdate, id=update_id)
    if update.status != PendingContentUpdate.Status.PENDING:
        return Response({"detail": "This update has already been resolved."}, status=400)
    vote_value = request.data.get("vote")
    if vote_value not in ("approve", "reject"):
        return Response({"detail": "vote must be 'approve' or 'reject'."}, status=400)
    PendingContentUpdateVote.objects.update_or_create(
        update=update, reviewer=request.user, defaults={"vote": vote_value}
    )
    return Response(resolve_content_update_votes(update))



@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_create_language(request):
    from apps.metadata.models import Language

    name = request.data.get("name", "").strip()

    if not name:
        return Response(
            {"detail": "Language name is required."},
            status=400
        )

    if Language.objects.filter(name__iexact=name).exists():
        return Response(
            {"detail": "A language with this name already exists."},
            status=400
        )

    language = Language.objects.create(
        name=name,
        status=Language.Status.APPROVED,
    )

    return Response(
        {
            "id": language.id,
            "name": language.name,
            "status": language.status,
        },
        status=201,
    )




@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_college(request, college_id):
    college = get_object_or_404(College, id=college_id)
    affected_users = UserProfile.objects.filter(college=college).count()
    name = college.name
    college.delete()

    ActivityLog.log(
        user=request.user,
        action="college_deleted",
        target_object=str(college_id),
        ip_address=get_client_ip(request),
        extra={"college_name": name, "affected_users": affected_users},
    )

    return Response(
        {"detail": f"College '{name}' deleted.", "affected_users": affected_users},
        status=200,
    )


@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_center_of_excellence(request, center_id):
    center = get_object_or_404(CenterOfExcellence, id=center_id)
    affected_users = UserProfile.objects.filter(center_of_excellence=center).count()
    name = center.name
    center.delete()

    ActivityLog.log(
        user=request.user,
        action="center_of_excellence_deleted",
        target_object=str(center_id),
        ip_address=get_client_ip(request),
        extra={"center_name": name, "affected_users": affected_users},
    )

    return Response(
        {"detail": f"Center of Excellence '{name}' deleted.", "affected_users": affected_users},
        status=200,
    )


@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_category(request, category_id):
    from django.db import transaction
    from apps.metadata.models import Category

    category = get_object_or_404(Category, id=category_id)
    name = category.name

    in_use_count = category.metadata_set.count()
    reassign_to_id = request.data.get("reassign_to")

    if in_use_count and not reassign_to_id:
        return Response(
            {
                "detail": (
                    f"'{name}' is still assigned to {in_use_count} dataset(s). "
                    "Pass 'reassign_to' with a category id to move those datasets "
                    "there before deleting, or leave them as-is and cancel."
                ),
                "in_use_count": in_use_count,
            },
            status=400,
        )

    if reassign_to_id:
        reassign_to = get_object_or_404(Category, id=reassign_to_id)
        if reassign_to.id == category.id:
            return Response(
                {"detail": "reassign_to must be a different category."},
                status=400,
            )

        with transaction.atomic():
            moved = category.metadata_set.update(category=reassign_to)
            category.delete()

        ActivityLog.log(
            user=request.user,
            action="category_deleted",
            target_object=str(category_id),
            ip_address=get_client_ip(request),
            extra={
                "category_name": name,
                "reassigned_datasets": moved,
                "reassigned_to": reassign_to.name,
            },
        )

        return Response(
            {
                "detail": f"Category '{name}' deleted. {moved} dataset(s) reassigned to '{reassign_to.name}'.",
            },
            status=200,
        )

    category.delete()

    ActivityLog.log(
        user=request.user,
        action="category_deleted",
        target_object=str(category_id),
        ip_address=get_client_ip(request),
        extra={"category_name": name},
    )

    return Response({"detail": f"Category '{name}' deleted."}, status=200)


@api_view(["DELETE"])
@permission_classes([IsAdminOnly])
def admin_delete_language(request, language_id):
    from apps.metadata.models import Language

    language = get_object_or_404(Language, id=language_id)
    name = language.name
    language.delete()

    ActivityLog.log(
        user=request.user,
        action="language_deleted",
        target_object=str(language_id),
        ip_address=get_client_ip(request),
        extra={"language_name": name},
    )

    return Response({"detail": f"Language '{name}' deleted."}, status=200)




@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_ban_user(request, user_id):
    from apps.accounts.models import BlockedCredential
    from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

    target_user = get_object_or_404(User, id=user_id)
    if target_user.id == request.user.id:
        return Response({"detail": "You can't ban your own account."}, status=400)

    reason = (request.data.get("reason") or "").strip()

    target_user.is_active = False
    target_user.save(update_fields=["is_active"])

    expires_at = BlockedCredential.block_user(target_user, admin=request.user, reason=reason)

    for token_obj in OutstandingToken.objects.filter(user=target_user):
        BlacklistedToken.objects.get_or_create(token=token_obj)

    ActivityLog.log(
        user=request.user,
        action="user_banned",
        target_object=str(target_user.id),
        ip_address=get_client_ip(request),
        extra={"banned_email": target_user.email, "reason": reason},
    )

    return Response(
        {
            "status": "banned",
            "blocked_until": expires_at.isoformat(),
        },
        status=200,
    )