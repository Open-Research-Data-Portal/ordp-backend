from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import UserProfile
import re
from .models import UserRole
from apps.notifications.services import notify
from apps.notifications.models import Notification
from django.utils import timezone
class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)
    stay_logged_in = serializers.BooleanField(default=False, required=False)

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


User = get_user_model()


class ProfileSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="profile.role", read_only=True)
    full_name = serializers.CharField(source="profile.full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name", "full_name", "role"]
        read_only_fields = ["id", "email", "username"]


class ExtendedProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile

        fields = [
            "full_name",
            "profile_picture",

            "affiliation",
            "college",
            "center_of_excellence",
            "department",
            "academia",
            "academic_title",
            "academic_rank",
            "highest_degree",
            "orcid_id",

            "research_interests",
            "bio",
            "additional_link",

            "profile_visibility",
            "terms_accepted",
        ]

    def save(self, **kwargs):
        instance = super().save(**kwargs)

        if instance.terms_accepted and not instance.terms_accepted_at:
            instance.terms_accepted_at = timezone.now()
            instance.save(update_fields=["terms_accepted_at"])

        # Automatically grant the researcher role
        # when all required profile fields are completed.
        if (
            instance.is_profile_complete()
            and not instance.has_role(UserRole.RoleChoice.RESEARCHER)
        ):
            UserRole.objects.get_or_create(
                profile=instance,
                role=UserRole.RoleChoice.RESEARCHER,
            )

            notify(
                user=instance.user,
                notification_type=Notification.NotificationType.RESEARCHER_APPROVED,
                message=(
                    "Your profile is complete — "
                    "you can now upload datasets."
                ),
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
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        validate_password(data["new_password"])
        return data
