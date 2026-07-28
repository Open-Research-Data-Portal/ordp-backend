from rest_framework import serializers
from django.contrib.auth import get_user_model

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


User = get_user_model()


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name"]
        read_only_fields = ["id", "email", "username"]