from rest_framework import serializers
from apps.datasets.serializers import DatasetSerializer
from .models import ModerationDecision


class ModerationDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModerationDecision
        fields = ["id", "dataset", "reviewer", "decision", "reason", "decided_at"]


class ModerationQueueItemSerializer(DatasetSerializer):
    pass