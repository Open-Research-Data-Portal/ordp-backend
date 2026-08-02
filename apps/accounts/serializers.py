from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import UserProfile


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


User = get_user_model()


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name"]
        read_only_fields = ["id", "email", "username"]


class ExtendedProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "college", "center_of_excellence", "department", "academia", "academic_title",
            "highest_degree", "orcid_id", "research_interests", "bio", "additional_link",
            "profile_visibility", "terms_accepted",
        ]

    def validate_terms_accepted(self, value):
        if not value:
            raise serializers.ValidationError("You must accept the terms to continue.")
        return value

    def save(self, **kwargs):
        instance = super().save(**kwargs)

        if instance.terms_accepted and not instance.terms_accepted_at:
            from django.utils import timezone
            instance.terms_accepted_at = timezone.now()
            instance.save(update_fields=["terms_accepted_at"])

        required = ["academia", "department"]
        is_complete = instance.terms_accepted and all(getattr(instance, f, None) for f in required)

        if is_complete and instance.role == UserProfile.Role.PUBLIC:
            from .models import ResearcherRequest
            from apps.notifications.services import notify
            from apps.notifications.models import Notification
            from django.contrib.auth import get_user_model

            req, _ = ResearcherRequest.objects.get_or_create(user=instance.user)
            if req.status != ResearcherRequest.Status.PENDING:
                req.status = ResearcherRequest.Status.PENDING
                req.reason = ""
                req.decided_by = None
                req.decided_at = None
                req.save()

            User = get_user_model()
            for admin_user in User.objects.filter(profile__role="admin"):
                notify(
                    user=admin_user, notification_type=Notification.NotificationType.RESEARCHER_REQUEST,
                    message=f"{instance.full_name} ({instance.user.email}) completed their profile and is requesting researcher access.",
                    link_path=f"/admin-panel/researcher-requests/{req.id}",
                )

        return instance

class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    username = serializers.CharField(min_length=2, max_length=60)
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        allowed_domains = ("@aastu.edu.et", "@aastustudent.edu.et")
        if not value.lower().endswith(allowed_domains):
            raise serializers.ValidationError("Only AASTU institutional emails are allowed.")
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_username(self, value):
        import re
        if not re.match(r'^[a-z0-9_]+$', value):
            raise serializers.ValidationError("Username may only contain lowercase letters, numbers, and underscores.")
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        return value

    def validate_password(self, value):
        validate_password(value)
        return value
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(data["new_password"])
        return data
