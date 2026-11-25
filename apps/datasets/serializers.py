from datetime import timedelta
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import serializers
from django.db.models import Count
from apps.accounts.models import ActivityLog
from apps.datasets.services.storage import presigned_download_url
from .models import (
    Dataset,
    DatasetFile,
    Contributor,
    PendingContentUpdate,
    DatasetVersion,
    ResourceIdentifier,
)

from apps.datasets.services.preview import CSV_FILE_TYPES, JSON_FILE_TYPES, preview_tabular_file

IMAGE_FILE_TYPES = {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp"}


def _request_user(serializer):
    request = serializer.context.get("request")
    return getattr(request, "user", None) if request else None


def file_preview_allowed(file_obj, user):
    """Public published files stay open. Other files need owner or dataset_file.view."""
    dataset = file_obj.dataset
    if (
        dataset.status == Dataset.Status.PUBLISHED
        and dataset.visibility == Dataset.Visibility.PUBLIC
        and not dataset.is_archived
    ):
        return True
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if dataset.is_owned_by(user):
        return True
    from apps.access_control.services import has_permission
    return has_permission(user, "dataset_file.view", "dataset_file", file_obj.id)

RECEIVED_DOWNLOAD_ACTIONS = ["owner_download", "contributor_download", "dataset_download", "reviewer_download"]
SERIES_WINDOW_DAYS = 30

def _daily_activity_series(dataset_id, actions, days=SERIES_WINDOW_DAYS):
    """Real daily counts for one dataset's activity, last `days` days —
    zero-filled so the frontend chart doesn't have gaps."""
    cutoff = (timezone.now() - timedelta(days=days)).date()
    grouped = (
        ActivityLog.objects.filter(
            target_object=f"Dataset:{dataset_id}", action__in=actions, timestamp__date__gte=cutoff,
        )
        .annotate(day=TruncDate("timestamp"))
        .values("day").annotate(count=Count("id")).order_by("day")
    )
    counts_by_day = {row["day"]: row["count"] for row in grouped}
    today = timezone.now().date()
    return [
        {"date": (cutoff + timedelta(days=i)).strftime("%m/%d"), "value": counts_by_day.get(cutoff + timedelta(days=i), 0)}
        for i in range((today - cutoff).days + 1)
    ]


def _delta_pct(dataset_id, actions, window_days=7):
    """% change comparing the last `window_days` vs. the `window_days` before that."""
    now = timezone.now()
    current_start = now - timedelta(days=window_days)
    previous_start = now - timedelta(days=window_days * 2)

    current = ActivityLog.objects.filter(
        target_object=f"Dataset:{dataset_id}", action__in=actions, timestamp__gte=current_start,
    ).count()
    previous = ActivityLog.objects.filter(
        target_object=f"Dataset:{dataset_id}", action__in=actions,
        timestamp__gte=previous_start, timestamp__lt=current_start,
    ).count()

    if previous == 0:
        return 100 if current > 0 else 0
    return round(((current - previous) / previous) * 100)
class DatasetFileSerializer(serializers.ModelSerializer):
    columns = serializers.JSONField(source="feature_names", read_only=True)
    preview_rows = serializers.SerializerMethodField()
    preview_url = serializers.SerializerMethodField()
    can_preview = serializers.SerializerMethodField()

    class Meta:
        model = DatasetFile
        fields = [
            "id", "original_filename", "file_type", "file_size", "checksum", "uploaded_at",
            "columns", "preview_rows", "preview_url", "can_preview",
        ]

    def get_can_preview(self, obj):
        return file_preview_allowed(obj, _request_user(self))

    def get_preview_rows(self, obj):
        if self.context.get("list_mode"):
            return None
        if not self.get_can_preview(obj):
            return None
        file_type = (obj.file_type or "").lower()
        if file_type not in (CSV_FILE_TYPES | JSON_FILE_TYPES):
            return None
        return preview_tabular_file(obj)

    def get_preview_url(self, obj):
        if not self.get_can_preview(obj):
            return None
        if (obj.file_type or "").lower() not in IMAGE_FILE_TYPES:
            return None
        return presigned_download_url(obj.file_key)


class ContributorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contributor
        fields = ["id", "user", "name", "contributor_type", "order"]


class ResourceIdentifierSerializer(serializers.ModelSerializer):
    official_doi_link = serializers.ReadOnlyField()
    release_version = serializers.SerializerMethodField()

    class Meta:
        model = ResourceIdentifier
        fields = [
            "id",
            "scheme",
            "role",
            "identifier_value",
            "provider",
            "lifecycle_status",
            "registration_status",
            "landing_page_url",
            "official_doi_link",
            "release_version",
            "registered_at",
        ]
        read_only_fields = fields

    def get_release_version(self, obj):
        return obj.release.version if obj.release_id else None


class DatasetSerializer(serializers.ModelSerializer):
    files = DatasetFileSerializer(many=True, read_only=True)
    contributors = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()
    thumbnail_url_expires_at = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    languages = serializers.SerializerMethodField()
    characteristics = serializers.SerializerMethodField()
    version = serializers.SerializerMethodField()
    owner_name = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    data_preview = serializers.SerializerMethodField()
    approve_votes = serializers.SerializerMethodField()
    ready_to_publish = serializers.SerializerMethodField()
    views_delta_pct = serializers.SerializerMethodField()
    downloads_delta_pct = serializers.SerializerMethodField()
    views_series = serializers.SerializerMethodField()
    downloads_series = serializers.SerializerMethodField()
    archive_status = serializers.SerializerMethodField()
    class Meta:
        model = Dataset
        fields = [
            "id",
            "title",
            "owner",
            "owner_name",
            "visibility",
            "status",
            "is_active",
            "is_archived",
            "archive_status",
            "archived_at",
            "version",
            "terms_accepted",
            "terms_version",
            "files",
            "contributors",
            "thumbnail_key",
            "thumbnail_url",
            "thumbnail_url_expires_at",
            "view_count",
            "download_count",
            # Dataset metadata
            "category",
            "description",
            "languages",
            "characteristics",
            "metadata",
            "data_preview",
            "approve_votes",
            "ready_to_publish",

            # Analytics
            "views_delta_pct",
            "downloads_delta_pct",
            "views_series",
            "downloads_series",

            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "owner",
            "status",
            "version",
            "is_active",
            "is_archived",
            "archived_at",
        ]

    def _published_metadata_snapshot(self, obj):
        release = getattr(obj, "published_release", None)
        if release and release.status == "published":
            from apps.datasets.services.snapshots import current_published_metadata_snapshot
            snapshot = current_published_metadata_snapshot(obj)
            if isinstance(snapshot, dict) and snapshot.get("schema_version") == release.snapshot_schema_version:
                return snapshot
        return None

    def get_contributors(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is None:
            return ContributorSerializer(obj.contributors.all(), many=True).data
        live_contributors = list(obj.contributors.all())
        result = []
        for item in snapshot.get("contributors", []):
            match = next((
                contributor for contributor in live_contributors
                if contributor.order == item.get("order")
                and (contributor.name or "").strip().casefold() == (item.get("name") or "").strip().casefold()
                and contributor.contributor_type == item.get("contributor_type")
            ), None)
            result.append({
                "id": str(match.pk) if match else None,
                "user": match.user_id if match else None,
                "name": item.get("name", ""),
                "contributor_type": item.get("contributor_type", "contributor"),
                "order": item.get("order", 1),
                "affiliation": item.get("affiliation", ""),
            })
        return result

    def get_category(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return (snapshot.get("category") or {}).get("name")
        try:
            return obj.metadata.category.name
        except Exception:
            return None

    def get_description(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return snapshot.get("description", "")
        try:
            return obj.metadata.description
        except Exception:
            return None

    def get_languages(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return snapshot.get("languages", [])
        try:
            return list(obj.metadata.languages.values_list("name", flat=True))
        except Exception:
            return []

    def get_characteristics(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return snapshot.get("characteristics", [])
        try:
            return list(obj.metadata.characteristics.values_list("name", flat=True))
        except Exception:
            return []

    def get_version(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return obj.published_release.version
        return obj.version

    def get_owner_name(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            return (snapshot.get("creator") or {}).get("name", "")
        profile = getattr(obj.owner, "profile", None)
        return getattr(profile, "full_name", "") if profile else ""

    def get_metadata(self, obj):
        snapshot = self._published_metadata_snapshot(obj)
        if snapshot is not None:
            category = snapshot.get("category") or {}
            coverage = snapshot.get("coverage") or {}
            methodology = snapshot.get("methodology") or {}
            citation = snapshot.get("citation") or {}
            resources = snapshot.get("resources") or {}
            return {
                "id": None,
                "description": snapshot.get("description", ""),
                "category": category.get("id"),
                "category_name": category.get("name"),
                "keywords": snapshot.get("keywords", []),
                "languages": snapshot.get("languages", []),
                "characteristics": snapshot.get("characteristics", []),
                "sponsor_or_grant": citation.get("sponsor_or_grant", ""),
                "doi_citation": citation.get("doi_citation", ""),
                "collaborators_text": citation.get("collaborators_text", ""),
                "related_resources": resources.get("related_resources", ""),
                "geographic_coverage": coverage.get("geographic", ""),
                "temporal_coverage": coverage.get("temporal", ""),
                "has_header": methodology.get("has_header"),
                "has_missing_values": methodology.get("has_missing_values"),
                "instances_represent": methodology.get("instances_represent", ""),
                "collection_method": methodology.get("collection_method", ""),
                "recommended_data_splits": methodology.get("recommended_data_splits", ""),
                "sensitive_data_disclosure": methodology.get("sensitive_data_disclosure", ""),
                "data_preprocessing": methodology.get("data_preprocessing", ""),
                "citation_notes": citation.get("citation_notes", ""),
            }
        if hasattr(obj, "metadata"):
            from apps.metadata.serializers import MetadataSerializer
            return MetadataSerializer(obj.metadata).data
        return None
    def _may_see_moderation(self, obj):
        user = _request_user(self)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        from apps.access_control.services import has_permission
        return (
            has_permission(user, "dataset.publish")
            or has_permission(user, "dataset.approve")
            or has_permission(user, "user.manage")
        )

    def get_approve_votes(self, obj):
        if not self._may_see_moderation(obj):
            return None
        return obj.moderation_decisions.filter(decision="approved").count()

    def get_ready_to_publish(self, obj):
        if obj.status != Dataset.Status.PENDING or not self._may_see_moderation(obj):
            return False
        return obj.moderation_decisions.filter(decision="approved").count() >= 2

    def get_data_preview(self, obj):
        # Dataset listings can contain many records. Reading each tabular file
        # from object storage here makes search/dashboard responses slow and
        # can exhaust a web worker. Detail views opt into the real preview.
        if not self.context.get("include_data_preview", True):
            return {"kind": "deferred", "available": False,
                    "reason": "Open the dataset to load its preview."}
        files = list(obj.files.all())
        if not files:
            return {"kind": "none", "available": False, "reason": "No files uploaded yet."}

        tabular_file = next(
            (f for f in files if (f.file_type or "").lower() in (CSV_FILE_TYPES | JSON_FILE_TYPES)),
            None,
        )
        if tabular_file:
            if not file_preview_allowed(tabular_file, _request_user(self)):
                return {"kind": "none", "available": False, "reason": "You do not have permission to view this file."}
            return {"kind": "tabular", **preview_tabular_file(tabular_file)}

        return {"kind": "unsupported", "available": False, "reason": "No preview available for this file type yet."}
    def get_thumbnail_url(self, obj):
        if not obj.thumbnail_key:
            return None

        return presigned_download_url(
            obj.thumbnail_key,
            expires_seconds=3600,
        )

    def get_thumbnail_url_expires_at(self, obj):
        if not obj.thumbnail_key:
            return None

        return timezone.now() + timedelta(hours=1)

    def get_views_delta_pct(self, obj):
        if self.context.get("list_mode"):
            return 0
        return _delta_pct(obj.id, ["dataset_view"])

    def get_downloads_delta_pct(self, obj):
        if self.context.get("list_mode"):
            return 0
        return _delta_pct(obj.id, RECEIVED_DOWNLOAD_ACTIONS)

    def get_views_series(self, obj):
        if self.context.get("list_mode"):
            return []
        return _daily_activity_series(obj.id, ["dataset_view"])

    def get_downloads_series(self, obj):
        if self.context.get("list_mode"):
            return []
        return _daily_activity_series(obj.id, RECEIVED_DOWNLOAD_ACTIONS)
    def get_archive_status(self, obj):
        from apps.admin_panel.models import DatasetArchiveRequest, DatasetUnarchiveRequest

        if self.context.get("list_mode"):
            return "archived" if obj.is_archived else "none"

        if obj.is_archived:
            if DatasetUnarchiveRequest.objects.filter(dataset=obj, status="pending").exists():
                return "unarchive_pending"
            return "archived"

        if DatasetArchiveRequest.objects.filter(dataset=obj, status="pending").exists():
            return "archive_pending"

        return "none"


class DatasetListSerializer(serializers.ModelSerializer):
    """Small payload for browse/search cards.

    Detail endpoints use DatasetSerializer. Keeping file previews, nested
    metadata, analytics, and contributor records out of list responses avoids
    one storage read/query chain per dataset.
    """
    owner_name = serializers.CharField(source="owner.profile.full_name", read_only=True)
    category = serializers.CharField(source="metadata.category.name", read_only=True, default=None)
    description = serializers.CharField(source="metadata.description", read_only=True, default=None)
    thumbnail_url = serializers.SerializerMethodField()
    file_count = serializers.IntegerField(source="files.count", read_only=True)

    class Meta:
        model = Dataset
        fields = [
            "id", "title", "owner", "owner_name", "visibility", "status",
            "is_active", "is_archived", "version", "thumbnail_key", "thumbnail_url",
            "view_count", "download_count", "category", "description", "file_count",
            "created_at", "updated_at",
        ]

    def get_thumbnail_url(self, obj):
        if not obj.thumbnail_key:
            return None
        return presigned_download_url(obj.thumbnail_key, expires_seconds=3600)

class InitUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)

    visibility = serializers.ChoiceField(
        choices=Dataset.Visibility.choices,
        required=False,
        allow_null=True,
        default=Dataset.Visibility.RESTRICTED,
    )


class PrepareUploadSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)

    file_size = serializers.IntegerField(
        min_value=1,
    )

    file_checksum = serializers.CharField(
        min_length=64,
        max_length=64,
    )

class TermsAcceptanceSerializer(serializers.Serializer):
    terms_accepted = serializers.BooleanField()




class RevisionComparisonSerializer(serializers.Serializer):
    dataset_title = serializers.CharField()
    submitted_by = serializers.CharField()
    submitted_at = serializers.DateTimeField()
    submitter_message = serializers.CharField()
    ai_change_summary = serializers.JSONField()
    diff_percentage = serializers.FloatField()
    will_trigger_content_review = serializers.BooleanField()
    previous_download_url = serializers.CharField()
    new_download_url = serializers.CharField()
    metadata_diff = serializers.JSONField()
    status = serializers.CharField()


class PendingContentUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PendingContentUpdate
        fields = ["id", "dataset", "source", "submitted_by", "approved_by_owner",
                  "diff_percentage", "change_summary", "proposed_metadata", "status", "created_at"]


class DatasetVersionSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.CharField(source="changed_by.profile.full_name", read_only=True)

    class Meta:
        model = DatasetVersion
        fields = ["id", "version_number", "file_key", "source", "changed_by", "changed_by_name",
                  "change_summary", "diff_percentage", "created_at"]
