from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
import re
from .models import UserProfile, BlockedCredential
from django.utils import timezone
from .services import validate_new_password
from apps.metadata.models import Category


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)
    stay_logged_in = serializers.BooleanField(default=False, required=False)

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


User = get_user_model()


class ProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(
        source="profile.full_name",
        read_only=True,
       
    )
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "full_name",
            "roles",
        ]
        read_only_fields = [
            "id",
            "email",
            "username",
            "full_name",
            "roles",
        ]

    def get_roles(self, obj):
        return list(
            obj.profile.roles.values_list("role", flat=True)
        )


class ExtendedProfileSerializer(serializers.ModelSerializer):

    interests = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Category.objects.filter(origin=Category.Origin.STANDARD),
        required=False,
    )

    class Meta:
        model = UserProfile
        fields = [
            "full_name",
            "profile_picture",
            "affiliation",
            "college",
            "center_of_excellence",
            "academia",
            "academia_other",
            "highest_degree_other",
            "academic_title",
            "academic_rank",
            "highest_degree",
            "orcid_id",
            "interests",
            "bio",
            "additional_link",
            "profile_visibility",
            "terms_accepted",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)

        data["interests"] = CategorySerializer(
    instance.interests.all(),
    many=True
).data

        return data
    def validate(self, attrs):
        academia = attrs.get(
            "academia",
            self.instance.academia if self.instance else "",
        )
        academia_other = attrs.get(
            "academia_other",
            self.instance.academia_other if self.instance else "",
        )

        highest_degree = attrs.get(
            "highest_degree",
            self.instance.highest_degree if self.instance else "",
        )
        highest_degree_other = attrs.get(
            "highest_degree_other",
            self.instance.highest_degree_other if self.instance else "",
        )

        if academia == UserProfile.Academia.OTHER:
            if not academia_other.strip():
                raise serializers.ValidationError({
                    "academia_other": "Please specify your academia."
                })
        else:
            attrs["academia_other"] = ""

        if highest_degree == UserProfile.HighestDegree.OTHER:
            if not highest_degree_other.strip():
                raise serializers.ValidationError({
                    "highest_degree_other": "Please specify your highest degree."
                })
        else:
            attrs["highest_degree_other"] = ""

        return attrs
    def save(self, **kwargs):
        instance = super().save(**kwargs)

        if instance.terms_accepted and not instance.terms_accepted_at:
            instance.terms_accepted_at = timezone.now()
            instance.save(update_fields=["terms_accepted_at"])

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
        if BlockedCredential.is_email_blocked(value):
            raise serializers.ValidationError(
                "This email can't be used to create an account right now. "
                "Contact support if you believe this is a mistake."
            )
        return value

    def validate_username(self, value):
        if not re.match(r'^[a-z0-9_]+$', value):
            raise serializers.ValidationError("Username may only contain lowercase letters, numbers, and underscores.")
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("This username is already taken.")
        if BlockedCredential.is_username_blocked(value):
            raise serializers.ValidationError(
                "This username can't be used to create an account right now."
            )
        return value

    def validate_password(self, value):
        try:
            validate_new_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
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
        try:
            validate_new_password(data["new_password"])
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages}) from exc
        return data


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = self.context["request"].user
        if not user.check_password(data["current_password"]):
            raise serializers.ValidationError({
                "current_password": "Current password is incorrect."
            })
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({
                "confirm_password": "Passwords do not match."
            })
        try:
            validate_new_password(data["new_password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages}) from exc
        return data



class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description"]

class PublicProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "full_name",
            "profile_picture",
            "affiliation",
            "college",
            "center_of_excellence",
            "academia",
            "academic_title",
            "academic_rank",
            "highest_degree",
            "orcid_id",
            "interests",
            "bio",
            "additional_link",
        ]
