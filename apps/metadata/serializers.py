from rest_framework import serializers
from .models import Category, Subject, Keyword, Metadata


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description"]


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name"]


class MetadataSerializer(serializers.ModelSerializer):
    class Meta:
        model = Metadata
        fields = ["id", "description", "category", "subject", "keywords", "sponsor_or_grant"]