from rest_framework import serializers
from .models import Category, Metadata, Keyword


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description"]
class KeywordsField(serializers.ListField):
    """
    keywords is a ManyToManyField on the model. Django gives us a
    ManyRelatedManager, not a list, so we must call .all() ourselves
    before treating it like an iterable of strings. Keyword's text
    field is called `word`, not `name`.
    """

    def to_representation(self, value):
        if hasattr(value, "all"):
            value = list(value.all().values_list("word", flat=True))
        return super().to_representation(value)

    def to_internal_value(self, data):
        # Allow either a real list (["a", "b"]) or a single free-text
        # string with comma-separated values ("a, b, c").
        if isinstance(data, str):
            data = [item.strip() for item in data.split(",") if item.strip()]
        data = super().to_internal_value(data)
        return [item.strip() for item in data if item.strip()]

class MetadataSerializer(serializers.ModelSerializer):
    keywords = KeywordsField(child=serializers.CharField(), required=False)
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        required=False,
        allow_null=True
    )
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Metadata
        fields = [
        "id",
        "description",
        "category",
        "category_name",
        "keywords",
        "sponsor_or_grant",
        "doi_citation",
        "collaborators_text",

        "related_resources",
        "geographic_coverage",
        "temporal_coverage",
        "has_header",
        "has_missing_values",
        "instances_represent",
        "collection_method",
        "recommended_data_splits",
        "sensitive_data_disclosure",
        "data_preprocessing",
        "citation_notes",
]

    def _set_keywords(self, instance, keyword_words):
        keyword_objs = [
            Keyword.objects.get_or_create(word=word)[0]
            for word in keyword_words
        ]
        instance.keywords.set(keyword_objs)

    def create(self, validated_data):
        keyword_words = validated_data.pop("keywords", [])
        instance = super().create(validated_data)
        self._set_keywords(instance, keyword_words)
        return instance

    def update(self, instance, validated_data):
        keyword_words = validated_data.pop("keywords", None)
        instance = super().update(instance, validated_data)
        if keyword_words is not None:
            self._set_keywords(instance, keyword_words)
        return instance