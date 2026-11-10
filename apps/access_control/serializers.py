from rest_framework import serializers

from .models import AccessRequest, Group, GroupMembership, Permission


class ScopeSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(
        choices=["platform", "dataset", "dataset_file", "project", "collection"],
    )
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


class BulkGrantSerializer(serializers.Serializer):
    grants = GrantSerializer(many=True, allow_empty=False)

    def validate_grants(self, value):
        keys = [(g["permission"].pk, g["resource_type"], g.get("resource_id")) for g in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Select each permission once for a resource.")
        return value


class GroupSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)
    permission_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Group
        fields = ["id", "name", "description", "created_at", "updated_at", "archived_at", "member_count", "permission_count"]
        read_only_fields = ["id", "created_at", "updated_at", "archived_at"]


class GroupCreateSerializer(GroupSerializer):
    members = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    grants = GrantSerializer(many=True, required=False)

    class Meta(GroupSerializer.Meta):
        fields = GroupSerializer.Meta.fields + ["members", "grants"]

    def validate_members(self, value):
        from django.contrib.auth import get_user_model
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Select each member once.")
        User = get_user_model()
        if User.objects.filter(pk__in=value, is_active=True).count() != len(value):
            raise serializers.ValidationError("Select active users only.")
        return value

    def validate_grants(self, value):
        keys = [(g["permission"].pk, g["resource_type"], g.get("resource_id")) for g in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Select each permission and resource once.")
        return value


class RequestCreateSerializer(ScopeSerializer):
    kind = serializers.ChoiceField(choices=AccessRequest.Kind.choices, default=AccessRequest.Kind.PERMISSION)
    group = serializers.PrimaryKeyRelatedField(queryset=Group.objects.filter(archived_at__isnull=True), required=False, allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)
    justification = serializers.CharField(min_length=10, max_length=4000)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        kind = attrs["kind"]
        group = attrs.get("group")
        codes = attrs.get("permissions", [])
        if kind == AccessRequest.Kind.GROUP_MEMBERSHIP:
            if not group or attrs["resource_type"] != "platform" or attrs.get("resource_id") or codes:
                raise serializers.ValidationError("Select a group without resource permissions.")
            user = self.context["request"].user
            if GroupMembership.objects.filter(group=group, user=user).exists():
                raise serializers.ValidationError("You are already a member of this group.")
            return attrs
        if kind == AccessRequest.Kind.GROUP_PERMISSION and not group:
            raise serializers.ValidationError("Select the group that needs these permissions.")
        if kind == AccessRequest.Kind.PERMISSION and group:
            raise serializers.ValidationError("Individual permission requests cannot target a group.")
        if not codes:
            raise serializers.ValidationError("Select at least one permission.")
        from .services import ENFORCED_PERMISSION_CODES
        if any(code not in ENFORCED_PERMISSION_CODES for code in codes):
            raise serializers.ValidationError("One or more permissions are not enforced by an endpoint yet.")
        if attrs["resource_type"] == "dataset":
            from apps.datasets.models import Dataset
            dataset = Dataset.objects.get(pk=attrs["resource_id"])
            if not dataset.is_active or dataset.visibility == Dataset.Visibility.PRIVATE or dataset.status != Dataset.Status.PUBLISHED:
                raise serializers.ValidationError("This dataset is not available for access requests.")
        if attrs["resource_type"] == "dataset_file":
            from apps.datasets.models import DatasetFile
            file_obj = DatasetFile.objects.select_related("dataset").get(pk=attrs["resource_id"])
            dataset = file_obj.dataset
            if not dataset.is_active or dataset.visibility == Dataset.Visibility.PRIVATE or dataset.status != Dataset.Status.PUBLISHED:
                raise serializers.ValidationError("This file is not available for access requests.")
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
    decisions = DecisionSerializer(many=True, required=False)
    decision = serializers.ChoiceField(choices=["approved", "rejected"], required=False)
    comment = serializers.CharField(max_length=2000, required=False, allow_blank=True)

    def validate_decisions(self, value):
        codes = [row["permission"].code for row in value]
        if len(set(codes)) != len(codes):
            raise serializers.ValidationError("Decide each permission once.")
        return value


class AccessRequestSerializer(serializers.ModelSerializer):
    requester_name = serializers.CharField(source="requester.profile.full_name", read_only=True)
    requester_email = serializers.EmailField(source="requester.email", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)
    items = serializers.SerializerMethodField()

    class Meta:
        model = AccessRequest
        fields = ["id", "requester", "requester_name", "requester_email", "kind", "group", "group_name", "resource_type", "resource_id", "justification", "status", "created_at", "expires_at", "reviewed_at", "review_comment", "items"]

    def get_items(self, obj):
        return [{"permission": item.permission_id, "name": item.permission.name,
                 "decision": item.decision, "reason": item.decision_reason,
                 "expires_at": item.approved_expiration} for item in obj.items.all()]
