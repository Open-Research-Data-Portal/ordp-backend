from django.db.models.aggregates import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta
from apps.accounts.models import ActivityLog
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from apps.accounts.permissions import IsCheckerOrAdmin
from apps.datasets.models import Dataset, PendingContentUpdate
from apps.sharing.models import DatasetAccessRequest, AccessRequestVote
from .models import ModerationDecision, DatasetDeletionRequest, DeletionRequestVote
from apps.accounts.permissions import IsAdminOnly
from apps.datasets.models import DatasetFile
import csv
from io import BytesIO
from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings
from apps.accounts.models import UserProfile
from django.http import HttpResponse
from reportlab.lib import colors # type: ignore
from reportlab.lib.pagesizes import landscape, letter # type: ignore
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle # type: ignore
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import IsAdminOnly
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
@permission_classes([IsCheckerOrAdmin])
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
@permission_classes([IsCheckerOrAdmin])
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
@permission_classes([IsCheckerOrAdmin])
def reviewer_guidelines(request):
    """Static reference info reviewers need while making a decision — the actual
    thresholds the system uses, so 'why did this need committee review' has an
    answer without digging through settings.py."""
    from django.conf import settings
    from apps.sharing.services import MIN_REVIEWER_QUORUM as SHARING_QUORUM
    from apps.admin_panel.services import MIN_REVIEWER_QUORUM as DELETION_QUORUM

    return Response({
        "moderation_guidelines": [
            "Check that the dataset's declared category and subject genuinely match its content.",
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
    role = request.data.get("role", UserProfile.Role.PUBLIC)

    if not email or not full_name:
        return Response({"detail": "email and full_name are required."}, status=400)
    if User.objects.filter(email=email).exists():
        return Response({"detail": "A user with this email already exists."}, status=400)

    user = User.objects.create(username=email, email=email, is_active=True)
    user.set_unusable_password()
    user.save()
    UserProfile.objects.create(user=user, full_name=full_name, role=role)  # signal grants matching UserRole

    from apps.accounts.views import password_reset_token
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = password_reset_token.make_token(user)
    set_password_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"
    send_mail(
        subject="Your ORDP account has been created",
        message=f"An admin created an account for you on ORDP. Set your password here: {set_password_link}",
        from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[email],
    )
    return Response({"status": "created", "user_id": user.id}, status=201)

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
    if role not in UserProfile.Role.values:
        return Response({"detail": "Invalid role."}, status=400)
    from apps.accounts.models import UserRole
    UserRole.objects.get_or_create(profile=target_user.profile, role=role)
    return Response({"status": "granted", "roles": list(target_user.profile.roles.values_list("role", flat=True))})


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


@api_view(["GET"])
@permission_classes([IsAdminOnly])
def pending_categories(request):
    from apps.metadata.models import Category
    qs = Category.objects.filter(status=Category.Status.PENDING).select_related("suggested_by__profile")
    return Response([{
        "id": c.id, "name": c.name,
        "suggested_by": c.suggested_by.profile.full_name if c.suggested_by else None,
    } for c in qs])


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def decide_pending_category(request, category_id):
    from apps.metadata.models import Category
    category = get_object_or_404(Category, id=category_id, status=Category.Status.PENDING)
    decision = request.data.get("decision")
    if decision == "approve":
        category.status = Category.Status.APPROVED
    elif decision == "reject":
        category.status = Category.Status.REJECTED
    else:
        return Response({"detail": "decision must be 'approve' or 'reject'."}, status=400)
    category.save(update_fields=["status"])
    return Response({"status": category.status})




@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_revoke_share_permission(request, permission_id):
    from apps.sharing.models import SharePermission
    from apps.sharing.services import revoke_share_permission
    permission = get_object_or_404(SharePermission, id=permission_id)
    revoke_share_permission(permission, request.user)
    return Response({"status": "revoked"})



@api_view(["GET"])
@permission_classes([IsAdminOnly])
def pending_languages(request):
    from apps.metadata.models import Language
    qs = Language.objects.filter(status=Language.Status.PENDING).select_related("suggested_by__profile")
    return Response([{
        "id": l.id, "name": l.name,
        "suggested_by": l.suggested_by.profile.full_name if l.suggested_by else None,
    } for l in qs])


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def decide_pending_language(request, language_id):
    from apps.metadata.models import Language
    language = get_object_or_404(Language, id=language_id, status=Language.Status.PENDING)
    decision = request.data.get("decision")
    if decision == "approve":
        language.status = Language.Status.APPROVED
    elif decision == "reject":
        language.status = Language.Status.REJECTED
    else:
        return Response({"detail": "decision must be 'approve' or 'reject'."}, status=400)
    language.save(update_fields=["status"])
    return Response({"status": language.status})