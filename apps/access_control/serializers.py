from rest_framework import serializers

from .models import AccessRequest, Group, Permission


class ScopeSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=["platform", "dataset", "dataset_file"])
    resource_id = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs):
        from .services import validate_scope
        try:
            validate_scope(attrs["resource_type"], attrs.get("resource_id"))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return attrs


class GrantSerializer(ScopeSerializer):
    permission = serializers.SlugRelatedField(slug_field="code", queryset=Permission.objects.all())
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["permission"].resource_type != attrs["resource_type"]:
            raise serializers.ValidationError("Permission and resource scope do not match.")
        return attrs


class GroupSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    permission_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "description", "created_at", "updated_at", "archived_at", "member_count", "permission_count"]
        read_only_fields = ["id", "created_at", "updated_at", "archived_at"]


class RequestCreateSerializer(ScopeSerializer):
    permissions = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    justification = serializers.CharField(min_length=10, max_length=4000)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["resource_type"] == "dataset":
            from apps.datasets.models import Dataset
            dataset = Dataset.objects.get(pk=attrs["resource_id"])
            if not dataset.is_active or dataset.visibility == Dataset.Visibility.PRIVATE or dataset.status != Dataset.Status.PUBLISHED:
                raise serializers.ValidationError("This dataset is not available for access requests.")
        if attrs["resource_type"] == "dataset_file":
            raise serializers.ValidationError("Request access to the dataset that contains this file.")
        codes = attrs["permissions"]
        if len(set(codes)) != len(codes):
            raise serializers.ValidationError("Select each permission once.")
        found = Permission.objects.filter(code__in=codes, resource_type=attrs["resource_type"])
        if found.count() != len(codes):
            raise serializers.ValidationError("One or more permissions do not apply to this resource.")
        return attrs


class DecisionSerializer(serializers.Serializer):
    permission = serializers.SlugRelatedField(slug_field="code", queryset=Permission.objects.all())
    decision = serializers.ChoiceField(choices=["approved", "rejected"])
    reason = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)


class ReviewSerializer(serializers.Serializer):
    decisions = DecisionSerializer(many=True, allow_empty=False)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    def validate_decisions(self, value):
        codes = [row["permission"].code for row in value]
        if len(set(codes)) != len(codes):
            raise serializers.ValidationError("Decide each permission once.")
        return value


class AccessRequestSerializer(serializers.ModelSerializer):
    requester_name = serializers.CharField(source="requester.profile.full_name", read_only=True)
    requester_email = serializers.EmailField(source="requester.email", read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = AccessRequest
        fields = ["id", "requester", "requester_name", "requester_email", "resource_type", "resource_id", "justification", "status", "created_at", "expires_at", "reviewed_at", "review_comment", "items"]

    def get_items(self, obj):
        return [{"permission": item.permission_id, "name": item.permission.name,
                 "decision": item.decision, "reason": item.decision_reason,
                 "expires_at": item.approved_expiration} for item in obj.items.all()]
