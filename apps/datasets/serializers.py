from rest_framework import serializers
from .models import Dataset, DatasetFile, Contributor, DatasetRevision, PendingContentUpdate, DatasetVersion


class DatasetFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetFile
        fields = ["id", "file_type", "file_size", "checksum", "uploaded_at"]


class ContributorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contributor
        fields = ["id", "user", "name", "contributor_type", "order"]


class DatasetSerializer(serializers.ModelSerializer):
    files = DatasetFileSerializer(many=True, read_only=True)
    contributors = ContributorSerializer(many=True, read_only=True)

    class Meta:
        model = Dataset
        fields = [
            "id", "title", "owner", "visibility", "status", "is_active", "version",
            "terms_accepted", "terms_version", "files", "contributors", "created_at", "updated_at",
        ]
        read_only_fields = ["owner", "status", "version", "is_active"]


class InitUploadSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    visibility = serializers.ChoiceField(choices=Dataset.Visibility.choices, default=Dataset.Visibility.RESTRICTED)


class TermsAcceptanceSerializer(serializers.Serializer):
    terms_accepted = serializers.BooleanField()

class DatasetRevisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetRevision
        fields = [
            "id", "dataset", "submitted_by", "diff_percentage", "triggered_version_bump",
            "submitter_message", "change_summary", "proposed_metadata", "status", "created_at",
        ]
        read_only_fields = ["diff_percentage", "triggered_version_bump", "change_summary", "status"]


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