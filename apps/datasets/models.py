import re
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import Lower
import secrets
from datetime import timedelta
from django.utils import timezone
from apps.datasets.services.pid_safety import sanitize_pid_json, sanitize_pid_text

class ChangeType(models.TextChoices):
    PATCH = "patch", "Patch"
    MINOR = "minor", "Minor"
    MAJOR = "major", "Major"

    @classmethod
    def rank(cls, value):
        # Lets us compute MAX(declared, computed, reviewer) later:
        # PATCH < MINOR < MAJOR.
        return {cls.PATCH: 0, cls.MINOR: 1, cls.MAJOR: 2}[value]


class DatasetQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "published_release" in kwargs or "published_release_id" in kwargs:
            raise RuntimeError("Use Dataset.save() to assign a published release.")
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if "published_release" in fields or "published_release_id" in fields:
            raise RuntimeError("Use Dataset.save() to assign a published release.")
        return super().bulk_update(objs, fields, batch_size=batch_size)


class Dataset(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public"
        PRIVATE = "private"
        RESTRICTED = "restricted"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Review"
        CHANGES_REQUESTED = "changes_requested", "Changes Requested"
        PUBLISHED = "published", "Published"
        REJECTED = "rejected", "Rejected"

    Status.APPROVED = Status.PUBLISHED

    def is_owned_by(self, user):
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if self.owner_id == user.id:
            return True
        return self.contributors.filter(user=user, contributor_type=Contributor.ContributorType.OWNER).exists()

    @property
    def persistent_identifier(self):
         return f"ordp:{self.pk}" if self.pk else None

    @property
    def current_version(self):
        if self.published_release_id:
            release = self.published_release
            if release.status == DatasetRelease.Status.PUBLISHED:
                release_file = (
                    release.files.select_related("stored_object")
                    .order_by("display_order", "id")
                    .first()
                )
                if release_file:
                    legacy_files = list(
                        self.files.select_related("stored_object").order_by("uploaded_at", "id")
                    )
                    release_keys = list(
                        release.files.select_related("stored_object")
                        .order_by("display_order", "id")
                        .values_list("stored_object__storage_key", flat=True)
                    )
                    legacy_keys = [file.file_key for file in legacy_files]
                    legacy_manifest_matches = (
                        all(
                            file.stored_object_id
                            and file.file_key == file.stored_object.storage_key
                            for file in legacy_files
                        )
                        and sorted(release_keys) == sorted(legacy_keys)
                    )
                    if legacy_manifest_matches:
                        return release_file.stored_object
                    if legacy_files:
                        return legacy_files[-1]
            return None
        if self.releases.exists():
            return None
        version = self.versions.first()
        if version:
            return version
        latest_file = self.files.order_by("-uploaded_at").first()
        if latest_file:
            return latest_file
        return None


    @property
    def has_pending_change(self):
        return (
            self.revision_requests.filter(status="approved", used=False).exists()
            or self.pending_updates.filter(status="pending").exists()
        )

    class ThumbnailSource(models.TextChoices):
        UPLOADED = "uploaded"
        FALLBACK_AUTO = "fallback_auto"
        FALLBACK_REVIEWER_SELECTED = "fallback_reviewer_selected"

    thumbnail_key = models.CharField(max_length=512, blank=True)
    thumbnail_source = models.CharField(max_length=32, choices=ThumbnailSource.choices, blank=True)
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="datasets")
    visibility = models.CharField(
    max_length=16,
    choices=Visibility.choices,
    null=True,
    blank=True,
    default=Visibility.RESTRICTED,
)
    status = models.CharField(max_length=18, choices=Status.choices, default=Status.DRAFT)
    is_active = models.BooleanField(default=True)
    version = models.IntegerField(default=1)
    view_count = models.PositiveIntegerField(default=0)
    download_count = models.PositiveIntegerField(default=0)
    edit_in_progress_notice = models.BooleanField(default=False)
    

    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=16, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    assigned_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_datasets",
    )
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    published_release = models.ForeignKey(
        "datasets.DatasetRelease",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    objects = models.Manager.from_queryset(DatasetQuerySet)()

    def save(self, *args, **kwargs):
        if self.published_release_id:
            release = DatasetRelease.objects.filter(
                pk=self.published_release_id,
                dataset_id=self.pk,
                status=DatasetRelease.Status.PUBLISHED,
            ).first()
            if (
                release is None
                or not release.files.exists()
                or not isinstance(release.metadata_snapshot, dict)
                or release.metadata_snapshot.get("schema_version")
                != release.snapshot_schema_version
                or not isinstance(release.citation_snapshot, dict)
                or not isinstance(release.license_snapshot, dict)
            ):
                raise ValidationError(
                    "published_release must reference a complete published release for this dataset."
                )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    


class MetadataEditAudit(models.Model):
    """Before/after audit for direct, non-release descriptive corrections."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, related_name="metadata_edit_audits"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="dataset_metadata_edits",
    )
    changed_fields = models.JSONField(default=list)
    before_snapshot = models.JSONField(default=dict)
    after_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["dataset", "created_at"], name="ds_metaaudit_ds_created_idx")]


class DatasetFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="files")
    file_key = models.CharField(max_length=512)
    original_filename = models.CharField(max_length=255, blank=True)
    file_type = models.CharField(max_length=32)
    file_size = models.BigIntegerField()
    checksum = models.CharField(max_length=128)
    is_structured = models.BooleanField(default=True)
    column_count = models.PositiveIntegerField(null=True, blank=True)   
    feature_names = models.JSONField(default=list, blank=True)          
    item_count = models.PositiveIntegerField(null=True, blank=True)     
    uploaded_at = models.DateTimeField(auto_now_add=True)
    stored_object = models.OneToOneField(
        "datasets.StoredObject",
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="legacy_file",
    )

class PermissionLevel(models.TextChoices):
    EDIT = "edit", "Can edit"
    VIEW = "view", "View only"


class Contributor(models.Model):
    class ContributorType(models.TextChoices):
        OWNER = "owner"
        CO_AUTHOR = "co_author"
        CONTRIBUTOR = "contributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="contributors")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255, blank=True)
    invited_email = models.EmailField(blank=True)
    contributor_type = models.CharField(max_length=16, choices=ContributorType.choices)
    permission = models.CharField(max_length=10, choices=PermissionLevel.choices, default=PermissionLevel.VIEW)
    order = models.IntegerField(default=1)

    def can_edit(self):
        return (
            self.contributor_type == Contributor.ContributorType.OWNER
            or (self.contributor_type == Contributor.ContributorType.CO_AUTHOR and self.permission == PermissionLevel.EDIT)
        )


def generate_invitation_token():
    return secrets.token_urlsafe(48)


class DatasetInvitation(models.Model):
    class Role(models.TextChoices):
        CO_AUTHOR = "co_author"
        CONTRIBUTOR = "contributor"

    class Status(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        EXPIRED = "expired"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="invitations")
    invited_email = models.EmailField()
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.CO_AUTHOR)
    permission = models.CharField(max_length=10, choices=PermissionLevel.choices, default=PermissionLevel.VIEW)
    token = models.CharField(max_length=64, unique=True, default=generate_invitation_token)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)

    def is_valid(self):
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
class DatasetRevision(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="revisions")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    previous_file_key = models.CharField(max_length=512)
    new_file_key = models.CharField(max_length=512)
    diff_percentage = models.FloatField()
    triggered_version_bump = models.BooleanField(default=False)
    submitter_message = models.TextField()
    change_summary = models.JSONField(default=dict, blank=True)
    proposed_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)


class PendingContentUpdate(models.Model):
    class Source(models.TextChoices):
        OWNER_EDIT = "owner_edit"
        CONTRIBUTOR_EDIT = "contributor_edit"
        REVISION = "revision"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="pending_updates")
    source = models.CharField(max_length=16, choices=Source.choices)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    approved_by_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    new_file_key = models.CharField(max_length=512)
    proposed_metadata = models.JSONField(default=dict, blank=True)
    diff_percentage = models.FloatField()
    change_summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class DatasetVersion(models.Model):
    class Source(models.TextChoices):
        OWNER_EDIT = "owner_edit"
        CONTRIBUTOR_EDIT = "contributor_edit"
        REVISION = "revision"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="versions")
    version_number = models.IntegerField()
    file_key = models.CharField(max_length=512)
    source = models.CharField(max_length=16, choices=Source.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    change_summary = models.JSONField(default=dict, blank=True)
    diff_percentage = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    
    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["dataset", "version_number"], name="unique_dataset_version_number")
        ]

    


class Bookmark(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks")
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="bookmarked_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["user", "dataset"]
        ordering = ["-created_at"]




class RevisionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"                    # waiting on owner decision
        AWAITING_COMMITTEE = "awaiting_committee"  # restricted only: owner approved, waiting on reviewers
        APPROVED = "approved"                  # final: requester may now propose a revision
        REJECTED = "rejected"                  # final: stopped, by owner or committee

    class OwnerDecision(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="revision_requests")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="revision_requests")
    reason = models.TextField()
    additional_justification = models.TextField(
        blank=True,
        help_text="Extra form required for restricted datasets, on top of `reason`.",
    )
    owner_decision = models.CharField(max_length=16, choices=OwnerDecision.choices, default=OwnerDecision.PENDING)
    owner_decided_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    token = models.CharField(max_length=64, unique=True, default=generate_invitation_token)
    used = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)



class RevisionRequestVote(models.Model):
    class Vote(models.TextChoices):
        APPROVE = "approve"
        REJECT = "reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision_request = models.ForeignKey(RevisionRequest, on_delete=models.CASCADE, related_name="votes")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vote = models.CharField(max_length=16, choices=Vote.choices)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["revision_request", "reviewer"]


class PendingContentUpdateVote(models.Model):
    class Vote(models.TextChoices):
        APPROVE = "approve"
        REJECT = "reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    update = models.ForeignKey(PendingContentUpdate, on_delete=models.CASCADE, related_name="votes")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vote = models.CharField(max_length=16, choices=Vote.choices)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["update", "reviewer"]


class DatasetWatcher(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="watchers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["dataset", "user"]




class UploadSession(models.Model):
    id = models.CharField(
        max_length=32,
        primary_key=True,
    )

    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )

    uploader = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dataset_upload_sessions",
    )

    proposal = models.ForeignKey(
        "datasets.DatasetProposal",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )

    original_filename = models.CharField(
        max_length=255,
        blank=True,
    )

    total_chunks = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    file_checksum = models.CharField(
        max_length=64,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.id

class StoredObject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="stored_objects")
    storage_key = models.CharField(max_length=512, unique=True)
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=32)
    file_size = models.BigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)
    verified_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    is_structured = models.BooleanField(default=True)
    column_count = models.PositiveIntegerField(null=True, blank=True)
    feature_names = models.JSONField(default=list, blank=True)
    item_count = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(file_size__gte=0),
                name="stored_object_nonnegative_size",
            ),
        ]

    @staticmethod
    def build_storage_key(dataset_id, object_id, filename):
        return f"objects/{dataset_id}/{object_id}/{filename}"

    @property
    def file_key(self):
        return self.storage_key

    def clean(self):
        super().clean()
        if self.file_size is not None and self.file_size < 0:
            raise ValidationError({"file_size": "file_size cannot be negative."})
        if self.checksum_sha256:
            if not re.match(r"^[0-9a-fA-F]{64}$", self.checksum_sha256):
                raise ValidationError({"checksum_sha256": "checksum_sha256 must be a 64-character hex string."})

    def save(self, *args, **kwargs):
        if self.checksum_sha256:
            self.checksum_sha256 = self.checksum_sha256.lower().strip()
        if self.file_size is not None and self.file_size < 0:
            raise ValidationError({"file_size": "file_size cannot be negative."})
        if self.checksum_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.checksum_sha256):
            raise ValidationError(
                {"checksum_sha256": "checksum_sha256 must be a 64-character lowercase hex string."}
            )
        if self.pk and not self._state.adding:
            original = StoredObject.objects.filter(pk=self.pk).values().first()
            if original:
                for field in self._meta.concrete_fields:
                    if field.attname == "verified_at":
                        continue
                    if field.attname == "uploaded_by_id" and getattr(self, field.attname) is None:
                        continue
                    if original[field.attname] != getattr(self, field.attname):
                        raise RuntimeError(
                            f"Field '{field.attname}' cannot be modified on a StoredObject."
                        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("StoredObject is immutable and cannot be deleted.", [self])


class DatasetReleaseQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "status" in kwargs:
            raise RuntimeError("Use DatasetRelease.save() for publication and withdrawal transitions.")
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if any(obj.status != DatasetRelease.Status.DRAFT for obj in objs):
            raise RuntimeError("DatasetRelease rows must be created in draft status.")
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        if "status" in fields:
            raise RuntimeError("Use DatasetRelease.save() for publication and withdrawal transitions.")
        return super().bulk_update(objs, fields, batch_size=batch_size)


class DatasetRelease(models.Model):
    # Existing fields omitted for brevity – they remain unchanged.
    # -------------------------------------------------------------------
    # PID/DOI infrastructure – new models are defined below the release model.
    # -------------------------------------------------------------------

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        WITHDRAWN = "withdrawn", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="releases")
    major = models.PositiveIntegerField()
    minor = models.PositiveIntegerField()
    patch = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    change_type = models.CharField(max_length=8, choices=ChangeType.choices, null=True, blank=True)
    change_summary = models.TextField(blank=True)
    release_notes = models.TextField(blank=True)

    snapshot_schema_version = models.PositiveIntegerField(default=1)
    metadata_snapshot = models.JSONField(default=dict)
    license_snapshot = models.JSONField(default=dict, blank=True)
    citation_snapshot = models.JSONField(default=dict, blank=True)

    base_release = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="derived_releases",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    published_at = models.DateTimeField(default=timezone.now)

    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    withdrawal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-major", "-minor", "-patch"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "major", "minor", "patch"],
                name="unique_release_version_per_dataset",
            ),
        ]

    objects = models.Manager.from_queryset(DatasetReleaseQuerySet)()

    @property
    def version(self):
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def persistent_identifier(self):
        if not self.pk or not self.dataset_id:
            return None
        return f"ordp:{self.dataset_id}:v{self.version}"

    def __str__(self):
        return f"{self.dataset_id} v{self.version}"

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("DatasetRelease is immutable and cannot be deleted.", [self])

    def save(self, *args, **kwargs):
        if self._state.adding and self.status != self.Status.DRAFT:
            raise RuntimeError("DatasetRelease rows must be created in draft status.")
        if self.pk and not self._state.adding:
            original = DatasetRelease.objects.filter(pk=self.pk).values().first()
            if original:
                status_change = (original["status"], self.status)
                if status_change == (self.Status.DRAFT, self.Status.PUBLISHED):
                    allowed_fields = {"status"}
                elif status_change == (self.Status.PUBLISHED, self.Status.WITHDRAWN):
                    allowed_fields = {
                        "status", "withdrawn_at", "withdrawn_by_id", "withdrawal_reason",
                    }
                else:
                    raise RuntimeError("DatasetRelease is immutable and cannot be modified.")

                for field in self._meta.concrete_fields:
                    if field.attname in allowed_fields:
                        continue
                    if (
                        field.attname in {"created_by_id", "published_by_id", "withdrawn_by_id"}
                        and getattr(self, field.attname) is None
                    ):
                        continue
                    if original[field.attname] != getattr(self, field.attname):
                        raise RuntimeError(
                            f"Field '{field.attname}' cannot be modified on a DatasetRelease."
                        )
        super().save(*args, **kwargs)

class DatasetReleaseFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    release = models.ForeignKey(DatasetRelease, on_delete=models.PROTECT, related_name="files")
    stored_object = models.ForeignKey(StoredObject, on_delete=models.PROTECT, related_name="release_entries")
    logical_file_id = models.UUIDField()
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order"]
        constraints = [
            models.UniqueConstraint(fields=["release", "logical_file_id"], name="unique_logical_file_per_release"),
            models.UniqueConstraint(fields=["release", "stored_object"], name="unique_object_per_release"),
        ]

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("DatasetReleaseFile is immutable and cannot be deleted.", [self])

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise RuntimeError("DatasetReleaseFile is immutable and cannot be modified.")
        super().save(*args, **kwargs)

class DatasetProposalQuerySet(models.QuerySet):
    CONTENT_FIELDS = {
        "dataset",
        "dataset_id",
        "base_release",
        "base_release_id",
        "source",
        "proposed_metadata",
        "proposed_license",
        "license_change_reason",
        "declared_change_type",
        "computed_min_change_type",
        "review_bypassed_at",
        "review_bypassed_by",
        "review_bypassed_by_id",
        "review_bypass_reason",
        "change_summary",
        "release_notes",
        "diff_percentage",
        "ai_summary",
        "submitted_by",
        "submitted_by_id",
    }

    def _lock_draft_proposals_for_content_edit(self):
        proposals = list(self.select_for_update().order_by("pk"))
        if any(proposal.status != DatasetProposal.Status.DRAFT for proposal in proposals):
            raise RuntimeError("Submitted proposal content is immutable.")

    def update(self, **kwargs):
        if "status" in kwargs:
            raise RuntimeError("Use the proposal lifecycle service to change status.")
        if self.CONTENT_FIELDS.intersection(kwargs):
            with transaction.atomic():
                self._lock_draft_proposals_for_content_edit()
                return super().update(**kwargs)
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if any(obj.status != DatasetProposal.Status.DRAFT for obj in objs):
            raise RuntimeError("DatasetProposal rows must be created in draft status.")
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        objs = list(objs)
        if "status" in fields:
            raise RuntimeError("Use the proposal lifecycle service to change status.")
        if self.CONTENT_FIELDS.intersection(fields):
            with transaction.atomic():
                self.model.objects.filter(pk__in=[obj.pk for obj in objs])._lock_draft_proposals_for_content_edit()
                return super().bulk_update(objs, fields, batch_size=batch_size)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def delete(self):
        if self.exclude(status=DatasetProposal.Status.DRAFT).exists():
            raise models.ProtectedError("Only draft proposals can be deleted.", [])
        return super().delete()

    def transition(self, proposal_id, new_status, **updates):
        allowed_transitions = {
            DatasetProposal.Status.DRAFT: {
                DatasetProposal.Status.PENDING_REVIEW,
                DatasetProposal.Status.APPROVED,  # audited automatic PATCH bypass only
                DatasetProposal.Status.CANCELLED,
                DatasetProposal.Status.STALE,
            },
            DatasetProposal.Status.PENDING_REVIEW: {
                DatasetProposal.Status.APPROVED,
                DatasetProposal.Status.REJECTED,
                DatasetProposal.Status.CHANGES_REQUESTED,
                DatasetProposal.Status.STALE,
                DatasetProposal.Status.CANCELLED,
            },
            DatasetProposal.Status.CHANGES_REQUESTED: {
                DatasetProposal.Status.DRAFT,
                DatasetProposal.Status.STALE,
                DatasetProposal.Status.CANCELLED,
            },
            DatasetProposal.Status.APPROVED: {
                DatasetProposal.Status.PUBLISHED,
                DatasetProposal.Status.STALE,
                DatasetProposal.Status.CANCELLED,
            },
        }
        with transaction.atomic(using=self.db):
            proposal = self.select_for_update().get(pk=proposal_id)
            if new_status not in allowed_transitions.get(proposal.status, set()):
                raise RuntimeError(
                    f"Invalid proposal status transition: {proposal.status} -> {new_status}."
                )

            if new_status == DatasetProposal.Status.APPROVED and proposal.status == DatasetProposal.Status.DRAFT:
                bypass_at = updates.get("review_bypassed_at")
                bypass_by = updates.get("review_bypassed_by_id")
                bypass_reason = updates.get("review_bypass_reason", "")
                minimum = updates.get("computed_min_change_type", proposal.computed_min_change_type)
                if not (bypass_at and bypass_by and bypass_reason and minimum == ChangeType.PATCH):
                    raise RuntimeError("A draft may bypass review only for an audited PATCH exemption.")
                if proposal.votes.exists():
                    raise RuntimeError("An automatic PATCH exemption cannot contain reviewer votes.")
            elif new_status in {DatasetProposal.Status.APPROVED, DatasetProposal.Status.REJECTED}:
                from apps.accounts.models import UserRole

                reviewer_count = UserRole.objects.filter(role="reviewer").values(
                    "profile_id"
                ).distinct().count()
                quorum = min(3, reviewer_count) or 1
                votes = proposal.votes.filter(
                    reviewer__profile__roles__role="reviewer"
                )
                approvals = votes.filter(vote=DatasetProposalVote.Vote.APPROVE).count()
                rejections = votes.filter(vote=DatasetProposalVote.Vote.REJECT).count()
                if approvals + rejections < quorum:
                    raise RuntimeError("Proposal reviewer quorum has not been reached.")
                if new_status == DatasetProposal.Status.APPROVED and approvals <= rejections:
                    raise RuntimeError("Proposal approval requires a reviewer majority.")
                if new_status == DatasetProposal.Status.REJECTED and rejections <= approvals:
                    raise RuntimeError("Proposal rejection requires a reviewer majority.")

            if new_status == DatasetProposal.Status.DRAFT and proposal.status == DatasetProposal.Status.CHANGES_REQUESTED:
                if updates.get("submitted_at", None) is not None or updates.get("decided_at", None) is not None:
                    raise RuntimeError("A resubmitted draft must clear prior submission and decision timestamps.")

            if new_status == DatasetProposal.Status.PENDING_REVIEW:
                computed_min_change_type = updates.get(
                    "computed_min_change_type", proposal.computed_min_change_type
                )
                if (
                    updates.get("submitted_at") is None
                    or computed_min_change_type not in ChangeType.values
                ):
                    raise RuntimeError(
                        "Submission requires a timestamp and computed minimum change type."
                    )

            if new_status == DatasetProposal.Status.PUBLISHED:
                release_id = updates.get("resulting_release_id")
                if not release_id or updates.get("published_at") is None:
                    raise RuntimeError("Publishing a proposal requires its resulting release.")
                if not DatasetRelease.objects.filter(
                    pk=release_id,
                    dataset_id=proposal.dataset_id,
                    status=DatasetRelease.Status.PUBLISHED,
                ).exists():
                    raise RuntimeError(
                        "The resulting release must be published for this dataset."
                    )

            return models.QuerySet.update(
                self.filter(pk=proposal_id, status=proposal.status),
                status=new_status,
                **updates,
            )

class DatasetProposal(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        PUBLISHED = "published", "Published"
        STALE = "stale", "Stale"
        CANCELLED = "cancelled", "Cancelled"

    class Source(models.TextChoices):
        OWNER_EDIT = "owner_edit", "Owner edit"
        CONTRIBUTOR_EDIT = "contributor_edit", "Contributor edit"
        REVISION_REQUEST = "revision_request", "Revision request"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="proposals")
    base_release = models.ForeignKey(DatasetRelease, on_delete=models.PROTECT, related_name="proposals")
    source = models.CharField(max_length=24, choices=Source.choices)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)

    proposed_metadata = models.JSONField(default=dict, blank=True)
    proposed_license = models.JSONField(default=dict, blank=True)
    license_change_reason = models.TextField(blank=True)

    declared_change_type = models.CharField(max_length=8, choices=ChangeType.choices, null=True, blank=True)
    computed_min_change_type = models.CharField(max_length=8, choices=ChangeType.choices, null=True, blank=True)
    final_change_type = models.CharField(max_length=8, choices=ChangeType.choices, null=True, blank=True)
    review_bypassed_at = models.DateTimeField(null=True, blank=True)
    review_bypassed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    review_bypass_reason = models.CharField(max_length=100, blank=True, default="")
    change_summary = models.TextField(blank=True)
    release_notes = models.TextField(blank=True)
    diff_percentage = models.FloatField(null=True, blank=True)
    ai_summary = models.JSONField(default=dict, blank=True)

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+",
    )
    resulting_release = models.OneToOneField(
        DatasetRelease, null=True, blank=True, on_delete=models.PROTECT, related_name="source_proposal",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    ACTIVE_STATUSES = ("draft", "pending_review", "changes_requested", "approved")

    objects = models.Manager.from_queryset(DatasetProposalQuerySet)()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["dataset", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset"],
                condition=models.Q(status__in=["draft", "pending_review", "changes_requested", "approved"]),
                name="one_active_proposal_per_dataset",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and self.status != self.Status.DRAFT:
            raise RuntimeError("DatasetProposal rows must be created in draft status.")
        if self.pk and not self._state.adding:
            using = kwargs.get("using") or self._state.db
            with transaction.atomic(using=using):
                original = (
                    type(self).objects.using(using)
                    .select_for_update()
                    .filter(pk=self.pk)
                    .values()
                    .first()
                )
                if original:
                    changed_content_fields = [
                        field.attname
                        for field in self._meta.concrete_fields
                        if field.attname in DatasetProposalQuerySet.CONTENT_FIELDS
                        and original[field.attname] != getattr(self, field.attname)
                    ]
                    if (
                        original["status"] != self.Status.DRAFT
                        and (changed_content_fields or self.status == self.Status.DRAFT)
                    ):
                        raise RuntimeError(
                            "Submitted proposal content and status are immutable."
                        )
                    if original["status"] != self.status:
                        raise RuntimeError(
                            "Use the proposal lifecycle service to change status."
                        )
                    return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        current_status = None
        if self.pk is not None:
            current_status = type(self).objects.filter(pk=self.pk).values_list(
                "status", flat=True
            ).first()
        if current_status is not None and current_status != self.Status.DRAFT:
            raise models.ProtectedError(
                "Only draft proposals can be deleted.", [self]
            )
        if self.pk is None:
            return super().delete(*args, **kwargs)
        return super().delete(*args, **kwargs)


class DatasetProposalFileQuerySet(models.QuerySet):
    def _lock_draft_proposals(self, proposal_ids):
        proposal_ids = set(proposal_ids)
        if not proposal_ids:
            return {}
        proposals = list(
            DatasetProposal.objects.select_for_update()
            .filter(pk__in=proposal_ids)
            .order_by("pk")
        )
        if len(proposals) != len(proposal_ids):
            raise RuntimeError("A proposal manifest references a missing proposal.")
        if any(proposal.status != DatasetProposal.Status.DRAFT for proposal in proposals):
            raise RuntimeError("Only draft proposal manifests can be modified.")
        return {proposal.pk: proposal.dataset_id for proposal in proposals}

    @staticmethod
    def _validate_stored_object_dataset(stored_object_id, proposal_datasets):
        stored_object_dataset_id = StoredObject.objects.filter(
            pk=stored_object_id
        ).values_list("dataset_id", flat=True).first()
        if stored_object_dataset_id is None or any(
            dataset_id != stored_object_dataset_id
            for dataset_id in proposal_datasets.values()
        ):
            raise RuntimeError(
                "A proposal file must reference a StoredObject from the same dataset."
            )

    def update(self, **kwargs):
        if "proposal" in kwargs or "proposal_id" in kwargs:
            raise RuntimeError("A proposal file cannot be moved to another proposal.")
        with transaction.atomic():
            proposal_ids = list(self.values_list("proposal_id", flat=True).distinct())
            proposal_datasets = self._lock_draft_proposals(proposal_ids)
            if "stored_object" in kwargs or "stored_object_id" in kwargs:
                stored_object = kwargs.get("stored_object", kwargs.get("stored_object_id"))
                stored_object_id = getattr(stored_object, "pk", stored_object)
                self._validate_stored_object_dataset(stored_object_id, proposal_datasets)
            return super().update(**kwargs)

    def delete(self):
        with transaction.atomic():
            proposal_ids = list(self.values_list("proposal_id", flat=True).distinct())
            self._lock_draft_proposals(proposal_ids)
            return super().delete()

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        with transaction.atomic():
            proposal_datasets = self._lock_draft_proposals(
                obj.proposal_id for obj in objs
            )
            for obj in objs:
                self._validate_stored_object_dataset(
                    obj.stored_object_id, {obj.proposal_id: proposal_datasets[obj.proposal_id]}
                )
            return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        objs = list(objs)
        if "proposal" in fields or "proposal_id" in fields:
            raise RuntimeError("A proposal file cannot be moved to another proposal.")
        with transaction.atomic():
            proposal_ids_by_file = dict(
                self.model.objects.filter(pk__in=[obj.pk for obj in objs]).values_list(
                    "pk", "proposal_id"
                )
            )
            if len(proposal_ids_by_file) != len(objs):
                raise RuntimeError("A proposal file no longer exists.")
            if any(
                obj.proposal_id != proposal_ids_by_file[obj.pk]
                for obj in objs
            ):
                raise RuntimeError("A proposal file cannot be moved to another proposal.")
            proposal_datasets = self._lock_draft_proposals(
                proposal_ids_by_file.values()
            )
            if "stored_object" in fields or "stored_object_id" in fields:
                for obj in objs:
                    self._validate_stored_object_dataset(
                        obj.stored_object_id,
                        {obj.proposal_id: proposal_datasets[obj.proposal_id]},
                    )
            return super().bulk_update(objs, fields, batch_size=batch_size)


class DatasetProposalFile(models.Model):
    """One row of a proposal's proposed file manifest."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(DatasetProposal, on_delete=models.CASCADE, related_name="files")
    stored_object = models.ForeignKey(StoredObject, on_delete=models.PROTECT, related_name="proposal_entries")
    logical_file_id = models.UUIDField()
    display_order = models.PositiveIntegerField(default=0)

    objects = models.Manager.from_queryset(DatasetProposalFileQuerySet)()

    class Meta:
        ordering = ["display_order"]
        constraints = [
            models.UniqueConstraint(fields=["proposal", "logical_file_id"], name="unique_logical_file_per_proposal"),
            models.UniqueConstraint(fields=["proposal", "stored_object"], name="unique_object_per_proposal"),
        ]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk and not self._state.adding:
                original_proposal_id = DatasetProposalFile.objects.filter(
                    pk=self.pk
                ).values_list("proposal_id", flat=True).first()
                if original_proposal_id and original_proposal_id != self.proposal_id:
                    raise RuntimeError("A proposal file cannot be moved to another proposal.")
            proposal_datasets = DatasetProposalFileQuerySet(
                model=DatasetProposalFile, using=self._state.db
            )._lock_draft_proposals([self.proposal_id])
            DatasetProposalFileQuerySet._validate_stored_object_dataset(
                self.stored_object_id, proposal_datasets
            )
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            proposal_datasets = DatasetProposalFileQuerySet(
                model=DatasetProposalFile, using=self._state.db
            )._lock_draft_proposals([self.proposal_id])
            return super().delete(*args, **kwargs)


class DatasetProposalVote(models.Model):
    class Vote(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(DatasetProposal, on_delete=models.CASCADE, related_name="votes")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    vote = models.CharField(max_length=16, choices=Vote.choices)
    reason = models.TextField(blank=True, default="")
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["proposal", "reviewer"], name="unique_vote_per_reviewer_per_proposal"),
            models.CheckConstraint(
                condition=~models.Q(vote="reject") | ~models.Q(reason=""),
                name="reject_vote_requires_reason",
            ),
        ]


    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise RuntimeError("Proposal vote decisions are immutable.")
        return super().save(*args, **kwargs)


class DatasetProposalActivity(models.Model):
    """Append-only audit trail for proposal decisions and review feedback."""
    class Action(models.TextChoices):
        CONTENT_UPDATED = "content_updated", "Content updated"
        SUBMITTED = "submitted", "Submitted"
        REOPENED_FOR_REVISION = "reopened_for_revision", "Reopened for revision"
        RESUBMITTED = "resubmitted", "Resubmitted"
        VOTE_CAST = "vote_cast", "Vote cast"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVIEW_BYPASSED = "review_bypassed", "Review bypassed"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"
        STALE = "stale", "Marked stale"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(DatasetProposal, on_delete=models.PROTECT, related_name="activity")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="dataset_proposal_activity")
    action = models.CharField(max_length=32, choices=Action.choices)
    from_status = models.CharField(max_length=24, blank=True, default="")
    to_status = models.CharField(max_length=24, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["proposal", "created_at"], name="ds_propact_prop_created_idx")]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise RuntimeError("Proposal activity records are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Proposal activity records are immutable.", [self])


class ContributorAttribution(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.PROTECT, related_name="attributions")
    release = models.ForeignKey(DatasetRelease, on_delete=models.PROTECT, related_name="attributions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    name = models.CharField(max_length=255)
    affiliation = models.CharField(max_length=255, blank=True)
    contribution_description = models.TextField(blank=True)
    attributed_at = models.DateTimeField(auto_now_add=True)


class ResourceIdentifier(models.Model):
    class Scheme(models.TextChoices):
        DOI = "doi", "DOI"
        ORDP = "ordp", "ORDP internal PID"

    class Role(models.TextChoices):
        CONCEPT = "concept", "Concept"
        VERSION = "version", "Version"

    class LifecycleStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class RegistrationStatus(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Not requested"
        PENDING = "pending", "Pending"
        REGISTERED = "registered", "Registered"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scheme = models.CharField(max_length=16, choices=Scheme.choices, default=Scheme.DOI)
    role = models.CharField(max_length=16, choices=Role.choices)
    dataset = models.ForeignKey(
        Dataset,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resource_identifiers",
    )
    release = models.ForeignKey(
        DatasetRelease,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="resource_identifiers",
    )
    identifier_value = models.CharField(max_length=512, null=True, blank=True)
    provider = models.CharField(max_length=64, blank=True)
    provider_info = models.JSONField(default=dict, blank=True)
    creation_source = models.CharField(max_length=64, blank=True, default="system")
    lifecycle_status = models.CharField(
        max_length=16,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.ACTIVE,
    )
    registration_status = models.CharField(
        max_length=20,
        choices=RegistrationStatus.choices,
        default=RegistrationStatus.NOT_REQUESTED,
    )
    landing_page_url = models.URLField(max_length=2048, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(role="concept", dataset__isnull=False, release__isnull=True)
                    | models.Q(role="version", dataset__isnull=True, release__isnull=False)
                ),
                name="resource_identifier_role_target_valid",
            ),
            models.UniqueConstraint(
                fields=["dataset", "scheme"],
                condition=models.Q(role="concept"),
                name="one_concept_identifier_per_dataset_scheme",
            ),
            models.UniqueConstraint(
                fields=["release", "scheme"],
                condition=models.Q(role="version"),
                name="one_version_identifier_per_release_scheme",
            ),
            models.UniqueConstraint(
                "scheme",
                Lower("identifier_value"),
                condition=~models.Q(identifier_value__isnull=True),
                name="unique_resource_identifier_value_ci",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scheme="ordp", identifier_value__isnull=False)
                    & ~models.Q(identifier_value="")
                    & models.Q(registration_status="not_requested")
                    & models.Q(registered_at__isnull=True)
                    & models.Q(provider="")
                ) | (
                    models.Q(scheme="doi")
                    & (
                        (
                            models.Q(registration_status="registered")
                            & models.Q(identifier_value__isnull=False)
                            & ~models.Q(identifier_value="")
                            & models.Q(registered_at__isnull=False)
                        ) | (
                            ~models.Q(registration_status="registered")
                            & models.Q(identifier_value__isnull=True)
                        )
                    )
                ),
                name="registered_identifier_has_value",
            ),
        ]
        indexes = [
            models.Index(fields=["dataset", "role"]),
            models.Index(fields=["release", "role"]),
        ]

    class IdentifierQuerySet(models.QuerySet):
        def update(self, **kwargs):
            raise RuntimeError("Resource identifiers must be changed through their lifecycle service.")

        def bulk_update(self, objs, fields, batch_size=None):
            raise RuntimeError("Resource identifiers must be changed through their lifecycle service.")

        def delete(self):
            raise models.ProtectedError(
                "Resource identifier history cannot be deleted.", self
            )

    objects = models.Manager.from_queryset(IdentifierQuerySet)()

    @property
    def official_doi_link(self):
        if (
            self.scheme == self.Scheme.DOI
            and self.registration_status == self.RegistrationStatus.REGISTERED
            and self.identifier_value
        ):
            return f"https://doi.org/{self.identifier_value}"
        return None

    def clean(self):
        super().clean()
        if self.role == self.Role.CONCEPT:
            if self.dataset_id is None or self.release_id is not None:
                raise ValidationError("Concept identifiers must target exactly one dataset.")
        elif self.role == self.Role.VERSION:
            if self.release_id is None or self.dataset_id is not None:
                raise ValidationError("Version identifiers must target exactly one release.")
        elif self.dataset_id is not None or self.release_id is not None:
            raise ValidationError("Identifier target does not match its role.")
        if self.scheme == self.Scheme.ORDP:
            if not self.identifier_value or self.registration_status != self.RegistrationStatus.NOT_REQUESTED:
                raise ValidationError("ORDP PIDs require an internal value and do not use DOI registration status.")
            if self.provider or self.registered_at:
                raise ValidationError("ORDP PIDs cannot carry external provider registration data.")
            if self.role == self.Role.CONCEPT and self.dataset_id and self.dataset.status != Dataset.Status.PUBLISHED:
                raise ValidationError("A Concept PID can only identify a published dataset.")
            if self.role == self.Role.VERSION and self.release_id and self.release.status not in {
                DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN
            }:
                raise ValidationError("A Version PID can only identify a published or withdrawn release.")
        elif (
            self.identifier_value
            and self.registration_status != self.RegistrationStatus.REGISTERED
        ):
            raise ValidationError(
                "A DOI value is recorded only after successful provider registration."
            )
        if self.scheme == self.Scheme.DOI and self.registration_status == self.RegistrationStatus.REGISTERED:
            if not self.identifier_value or not self.registered_at:
                raise ValidationError(
                    "Registered identifiers require a provider-assigned value and timestamp."
                )

    def save(self, *args, **kwargs):
        self.provider_info = sanitize_pid_json(self.provider_info)
        if self.identifier_value is not None:
            self.identifier_value = self.identifier_value.strip() or None
        if self._state.adding and self.registration_status == self.RegistrationStatus.REGISTERED:
            raise ValidationError(
                "Only a successful provider registration can mark an identifier registered."
            )
        if self.pk and not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values().first()
            if original:
                immutable_fields = (
                    "scheme",
                    "role",
                    "dataset_id",
                    "release_id",
                    "creation_source",
                )
                if any(original[field] != getattr(self, field) for field in immutable_fields):
                    raise RuntimeError("A resource identifier's scheme, role, and target are immutable.")
                if original["identifier_value"] is not None and (
                    original["identifier_value"] != self.identifier_value
                ):
                    raise RuntimeError("A persistent identifier value cannot be changed.")
                if (
                    original["scheme"] == self.Scheme.ORDP
                    and original["landing_page_url"] != self.landing_page_url
                ):
                    raise RuntimeError("An ORDP PID resolver URL cannot be reassigned.")
                if (
                    original["registration_status"] == self.RegistrationStatus.REGISTERED
                    and (
                        self.registration_status != self.RegistrationStatus.REGISTERED
                        or original["registered_at"] != self.registered_at
                        or original["provider"] != self.provider
                    )
                ):
                    raise RuntimeError("Registered identifier history cannot be erased.")
                if (
                    original["registration_status"] != self.RegistrationStatus.REGISTERED
                    and self.registration_status == self.RegistrationStatus.REGISTERED
                    and not DOIRegistrationAttempt.objects.filter(
                        identifier_id=self.pk,
                        status=DOIRegistrationAttempt.Status.SUCCEEDED,
                    ).exists()
                ):
                    raise ValidationError(
                        "A successful provider attempt is required to register an identifier."
                    )
                if (
                    original["lifecycle_status"] == self.LifecycleStatus.WITHDRAWN
                    and self.lifecycle_status != self.LifecycleStatus.WITHDRAWN
                ):
                    raise RuntimeError("A withdrawn resource identifier cannot be reactivated.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError(
            "Resource identifier history cannot be deleted.", [self]
        )


class ResourceRelationship(models.Model):
    class Type(models.TextChoices):
        HAS_VERSION = "hasVersion", "Has version"
        IS_VERSION_OF = "isVersionOf", "Is version of"
        IS_RELEASE_OF = "isReleaseOf", "Is release of"
        IS_NEW_VERSION_OF = "isNewVersionOf", "Is new version of"
        IS_PREVIOUS_VERSION_OF = "isPreviousVersionOf", "Is previous version of"
        IS_PART_OF = "isPartOf", "Is part of"
        IS_DERIVED_FROM = "isDerivedFrom", "Is derived from"
        IS_SUPPLEMENT_TO = "isSupplementTo", "Is supplement to"
        IS_SUPPLEMENTED_BY = "isSupplementedBy", "Is supplemented by"
        IS_IDENTICAL_TO = "isIdenticalTo", "Is identical to"
        RELATED_TO = "relatedTo", "Related to"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_identifier = models.ForeignKey(
        ResourceIdentifier,
        on_delete=models.PROTECT,
        related_name="outgoing_relationships",
    )
    related_identifier = models.ForeignKey(
        ResourceIdentifier,
        on_delete=models.PROTECT,
        related_name="incoming_relationships",
    )
    relationship_type = models.CharField(max_length=32, choices=Type.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(subject_identifier=models.F("related_identifier")),
                name="resource_relationship_not_self",
            ),
            models.UniqueConstraint(
                fields=["subject_identifier", "related_identifier", "relationship_type"],
                name="unique_resource_relationship",
            ),
        ]

    class RelationshipQuerySet(models.QuerySet):
        def update(self, **kwargs):
            raise RuntimeError("Resource relationships are immutable.")

        def bulk_update(self, objs, fields, batch_size=None):
            raise RuntimeError("Resource relationships are immutable.")

        def delete(self):
            raise models.ProtectedError("Resource relationship history cannot be deleted.", self)

    objects = models.Manager.from_queryset(RelationshipQuerySet)()

    def clean(self):
        super().clean()
        if self.subject_identifier_id == self.related_identifier_id:
            raise ValidationError("An identifier cannot be related to itself.")
        subject = self.subject_identifier
        related = self.related_identifier

        def dataset_id(identifier):
            if identifier.role == ResourceIdentifier.Role.CONCEPT:
                return identifier.dataset_id
            return identifier.release.dataset_id

        subject_dataset = dataset_id(subject)
        related_dataset = dataset_id(related)
        same_dataset = subject_dataset == related_dataset
        relation = self.relationship_type

        if subject.scheme != related.scheme:
            raise ValidationError("Related identifiers must use the same identifier scheme.")

        if relation == self.Type.HAS_VERSION:
            valid = subject.role == ResourceIdentifier.Role.CONCEPT and related.role == ResourceIdentifier.Role.VERSION and same_dataset
        elif relation in {self.Type.IS_VERSION_OF, self.Type.IS_RELEASE_OF}:
            valid = subject.role == ResourceIdentifier.Role.VERSION and related.role == ResourceIdentifier.Role.CONCEPT and same_dataset
        elif relation in {self.Type.IS_NEW_VERSION_OF, self.Type.IS_PREVIOUS_VERSION_OF}:
            valid = subject.role == related.role == ResourceIdentifier.Role.VERSION and same_dataset
            if valid:
                left = (subject.release.major, subject.release.minor, subject.release.patch)
                right = (related.release.major, related.release.minor, related.release.patch)
                valid = left > right if relation == self.Type.IS_NEW_VERSION_OF else left < right
        elif relation in {
            self.Type.IS_PART_OF, self.Type.IS_DERIVED_FROM,
            self.Type.IS_SUPPLEMENT_TO, self.Type.IS_SUPPLEMENTED_BY,
            self.Type.IS_IDENTICAL_TO, self.Type.RELATED_TO,
        }:
            valid = True
        else:
            valid = False

        if not valid:
            raise ValidationError("Relationship type does not match identifier roles, dataset scope, or version order.")

        inverse_conflicts = {
            self.Type.HAS_VERSION: {self.Type.IS_VERSION_OF, self.Type.IS_RELEASE_OF},
            self.Type.IS_VERSION_OF: {self.Type.HAS_VERSION},
            self.Type.IS_RELEASE_OF: {self.Type.HAS_VERSION, self.Type.IS_VERSION_OF},
            self.Type.IS_NEW_VERSION_OF: {self.Type.IS_PREVIOUS_VERSION_OF},
            self.Type.IS_PREVIOUS_VERSION_OF: {self.Type.IS_NEW_VERSION_OF},
        }
        if self._state.adding and ResourceRelationship.objects.filter(
            subject_identifier=subject,
            related_identifier=related,
            relationship_type__in=inverse_conflicts.get(relation, set()),
        ).exists():
            raise ValidationError("A contradictory relationship already exists for these resources.")

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise RuntimeError("Resource relationships are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("Resource relationship history cannot be deleted.", [self])


class DOIRegistrationAttempt(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    identifier = models.ForeignKey(
        ResourceIdentifier,
        on_delete=models.PROTECT,
        related_name="registration_attempts",
    )
    attempt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempted_at = models.DateTimeField(default=timezone.now)
    request_summary = models.JSONField(default=dict, blank=True)
    response_summary = models.JSONField(default=dict, blank=True)
    error_information = models.TextField(blank=True)

    class Meta:
        ordering = ["attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["identifier", "attempt_number"],
                name="unique_identifier_registration_attempt",
            ),
        ]

    def save(self, *args, **kwargs):
        self.request_summary = sanitize_pid_json(self.request_summary)
        self.response_summary = sanitize_pid_json(self.response_summary)
        self.error_information = sanitize_pid_text(self.error_information)
        if self.pk and not self._state.adding:
            original = type(self).objects.filter(pk=self.pk).values().first()
            if original:
                immutable = ("identifier_id", "attempt_number", "attempted_at", "request_summary")
                if any(original[field] != getattr(self, field) for field in immutable):
                    raise RuntimeError("DOI registration attempt identity is immutable.")
                if original["status"] != self.Status.PENDING:
                    raise RuntimeError("A terminal DOI registration attempt is immutable.")
                if self.status == self.Status.PENDING:
                    raise RuntimeError("A pending DOI registration attempt must transition to a terminal state.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError("DOI registration attempt history is immutable.", [self])



class PIDRegistrationEvent(models.Model):
    """Append-only database audit of important PID outbox transitions."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    sequence = models.BigIntegerField(
        unique=True,
        editable=False,
    )
    event_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=64)
    from_status = models.CharField(
        max_length=16,
        blank=True,
        default="",
    )
    to_status = models.CharField(max_length=16)
    retry_count = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        indexes = [
            models.Index(
                fields=["event_id", "created_at"],
                name="pid_event_audit_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise RuntimeError(
                "PID registration audit events are immutable."
            )
        self.detail = sanitize_pid_text(self.detail)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise models.ProtectedError(
            "PID registration audit events are immutable.",
            [self],
        )


class OutboxEvent(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=255, unique=True)
    retry_count = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    locked_by = models.UUIDField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at"]),
            models.Index(fields=["status", "locked_until"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(retry_count__gte=0),
                name="pid_outbox_nonnegative_retry_count",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status="processing")
                    | (
                        models.Q(locked_at__isnull=False)
                        & models.Q(locked_until__isnull=False)
                        & models.Q(locked_by__isnull=False)
                    )
                ),
                name="processing_outbox_event_has_lease",
            ),
        ]

    def save(self, *args, **kwargs):
        self.payload = sanitize_pid_json(self.payload)
        self.last_error = sanitize_pid_text(self.last_error)
        return super().save(*args, **kwargs)
