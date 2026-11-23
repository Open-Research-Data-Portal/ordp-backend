from rest_framework import serializers
from apps.ai_integration.models import FileAnalysis


class CheckResultSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=64)
    result = serializers.ChoiceField(choices=["pass", "warn", "fail", "skipped"])
    detail = serializers.CharField(allow_blank=True, required=False, default="")


class TechnicalMetaSerializer(serializers.Serializer):
    size_bytes = serializers.IntegerField(required=False)
    sha256 = serializers.CharField(required=False, max_length=64)


class AnalysisResultInputSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["done", "partial", "failed"])
    level = serializers.ChoiceField(choices=["A", "B1", "B2", "C"], allow_null=True, required=False)
    detected_type = serializers.CharField(max_length=64, allow_blank=True, required=False, default="")
    extension_matches = serializers.BooleanField(required=False, default=True)
    coverage = serializers.ChoiceField(choices=["fully_analysed", "partially_analysed"], required=False, default="fully_analysed")
    checks = CheckResultSerializer(many=True, required=False, default=list)
    warnings = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    technical = serializers.DictField(required=False, default=dict)
    error = serializers.DictField(allow_null=True, required=False, default=None)


class FileAnalysisPublicSerializer(serializers.ModelSerializer):
    file_id = serializers.SerializerMethodField()

    class Meta:
        model = FileAnalysis
        fields = [
            "file_id",
            "status",
            "level",
            "coverage",
            "detected_type",
            "checks",
            "warnings",
            "technical",
            "error",
        ]

    def get_file_id(self, obj):
        return str(obj.stored_object_id)
