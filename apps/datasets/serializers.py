from rest_framework import serializers
from .models import Dataset, DatasetFile, Contributor, DatasetRevision, PendingContentUpdate, DatasetVersion


class DatasetFileSerializer(serializers.ModelSerializer):
    columns = serializers.JSONField(source="feature_names", read_only=True)
    preview_rows = serializers.SerializerMethodField()

    class Meta:
        model = DatasetFile
        fields = ["id", "file_type", "file_size", "checksum", "uploaded_at", "columns", "preview_rows"]

    def get_preview_rows(self, obj):
        # Return mock or stored preview rows if any, or default sample rows
        return getattr(obj, "preview_rows_data", [
            ["T-001", "07:15:00", "08:02:00", "Bus", "12.4"],
            ["T-002", "07:45:00", "08:30:00", "LRT", "8.1"],
        ])


class ContributorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contributor
        fields = ["id", "user", "name", "contributor_type", "order"]


class DatasetSerializer(serializers.ModelSerializer):
    files = DatasetFileSerializer(many=True, read_only=True)
    contributors = ContributorSerializer(many=True, read_only=True)
    owner_name = serializers.CharField(source="owner.profile.full_name", read_only=True)
    metadata = serializers.SerializerMethodField()
    views_delta_pct = serializers.SerializerMethodField()
    downloads_delta_pct = serializers.SerializerMethodField()
    views_series = serializers.SerializerMethodField()
    downloads_series = serializers.SerializerMethodField()

    class Meta:
        model = Dataset
        fields = [
            "id", "title", "owner", "owner_name", "visibility", "status", "is_active", "version",
            "terms_accepted", "terms_version", "thumbnail_key", "view_count", "download_count",
            "files", "contributors", "metadata", "views_delta_pct", "downloads_delta_pct",
            "views_series", "downloads_series", "created_at", "updated_at",
        ]
        read_only_fields = ["owner", "status", "version", "is_active"]

    def get_metadata(self, obj):
        if hasattr(obj, 'metadata'):
            from apps.metadata.serializers import MetadataSerializer
            return MetadataSerializer(obj.metadata).data
        return None

    def get_views_delta_pct(self, obj):
        return 12

    def get_downloads_delta_pct(self, obj):
        return 8

    def get_views_series(self, obj):
        return [
            { "date": "07/27", "value": 118 }, { "date": "07/29", "value": 160 },
            { "date": "07/31", "value": 170 }, { "date": "08/02", "value": 140 },
            { "date": "08/03", "value": 150 }, { "date": "08/05", "value": 110 },
            { "date": "08/06", "value": 195 }, { "date": "08/08", "value": 145 },
            { "date": "08/09", "value": 205 }, { "date": "08/10", "value": 150 },
            { "date": "08/11", "value": 130 }, { "date": "08/12", "value": 165 },
            { "date": "08/13", "value": 150 }, { "date": "08/14", "value": 195 },
            { "date": "08/15", "value": 105 }, { "date": "08/17", "value": 150 },
            { "date": "08/18", "value": 165 }, { "date": "08/19", "value": 190 },
            { "date": "08/20", "value": 155 }, { "date": "08/21", "value": 105 },
        ]

    def get_downloads_series(self, obj):
        return [
            { "date": "07/27", "value": 35 }, { "date": "07/29", "value": 63 },
            { "date": "07/31", "value": 48 }, { "date": "08/02", "value": 48 },
            { "date": "08/03", "value": 44 }, { "date": "08/05", "value": 47 },
            { "date": "08/06", "value": 37 }, { "date": "08/08", "value": 37 },
            { "date": "08/09", "value": 58 }, { "date": "08/10", "value": 40 },
            { "date": "08/11", "value": 38 }, { "date": "08/12", "value": 69 },
            { "date": "08/13", "value": 58 }, { "date": "08/14", "value": 62 },
            { "date": "08/15", "value": 39 }, { "date": "08/16", "value": 45 },
            { "date": "08/17", "value": 53 }, { "date": "08/18", "value": 46 },
            { "date": "08/19", "value": 78 }, { "date": "08/20", "value": 41 },
            { "date": "08/21", "value": 39 }, { "date": "08/22", "value": 51 },
            { "date": "08/23", "value": 55 }, { "date": "08/24", "value": 55 },
            { "date": "08/25", "value": 56 }, { "date": "08/26", "value": 40 },
            { "date": "08/27", "value": 33 },
        ]


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