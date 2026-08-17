from rest_framework import serializers
from .models import DatasetAccessRequest


class RequestAccessSerializer(serializers.Serializer):
    purpose = serializers.CharField(help_text="Usability form — collected for every visibility tier.")
    purpose_type = serializers.ChoiceField(
        choices=DatasetAccessRequest.PurposeType.choices, default=DatasetAccessRequest.PurposeType.READ,
        help_text="'edit' triggers the edit-in-progress notice on the dataset if the committee approves.",
    )
    justification = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Second form, required only when the dataset's visibility is 'restricted'.",
    )


class DatasetAccessRequestSerializer(serializers.ModelSerializer):
    approve_votes = serializers.SerializerMethodField()
    reject_votes = serializers.SerializerMethodField()

    class Meta:
        model = DatasetAccessRequest
        fields = ["id", "dataset", "requester", "purpose_type", "status",
                  "approve_votes", "reject_votes", "created_at", "resolved_at"]

    def get_approve_votes(self, obj):
        return obj.votes.filter(vote="approve").count()

    def get_reject_votes(self, obj):
        return obj.votes.filter(vote="reject").count()


class InviteContributorSerializer(serializers.Serializer):
    email = serializers.EmailField()
    contributor_type = serializers.ChoiceField(choices=["author", "contributor"], default="contributor")