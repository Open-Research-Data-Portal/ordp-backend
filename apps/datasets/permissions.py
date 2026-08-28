from rest_framework.permissions import BasePermission


class IsDatasetOwner(BasePermission):
    def has_permission(self, request, view):
        from .models import Dataset, DatasetRevision, PendingContentUpdate
        dataset_id = view.kwargs.get("dataset_id")
        if dataset_id:
            dataset = Dataset.objects.filter(id=dataset_id).first()
            if dataset and dataset.is_owned_by(request.user):
                return True
            self.message = "Only the dataset owner can do this."
            return False

        revision_id = view.kwargs.get("revision_id") or view.kwargs.get("update_id")
        if revision_id:
            revision = (DatasetRevision.objects.filter(id=revision_id).first()
                        or PendingContentUpdate.objects.filter(id=revision_id).first())
            if revision and revision.dataset.is_owned_by(request.user):
                return True
            self.message = "Only the dataset owner can do this."
            return False
        return False

    def has_object_permission(self, request, view, obj):
        dataset = obj if hasattr(obj, "owner") else obj.dataset
        if dataset.is_owned_by(request.user):
            return True
        self.message = "Only the dataset owner can do this."
        return False


class IsDatasetOwnerOrContributor(BasePermission):
    def has_permission(self, request, view):
        from .models import Dataset, Contributor

        dataset_id = view.kwargs.get("dataset_id")

        if not dataset_id:
            return False

        # Dataset owner
        if Dataset.objects.filter(
            id=dataset_id,
            owner=request.user
        ).exists():
            return True

        # Contributor to this specific dataset
        return Contributor.objects.filter(
            dataset_id=dataset_id,
            user=request.user
        ).exists()