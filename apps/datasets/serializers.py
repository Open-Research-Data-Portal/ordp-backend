from rest_framework import serializers
from .models import Dataset, DatasetFile, Contributor


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