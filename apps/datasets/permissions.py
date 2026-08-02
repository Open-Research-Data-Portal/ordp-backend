from rest_framework.permissions import BasePermission


class IsDatasetOwner(BasePermission):
    def has_permission(self, request, view):
        from .models import Dataset, DatasetRevision, PendingContentUpdate
        dataset_id = view.kwargs.get("dataset_id")
        if dataset_id:
            return Dataset.objects.filter(id=dataset_id, owner=request.user).exists()

        revision_id = view.kwargs.get("revision_id") or view.kwargs.get("update_id")
        if revision_id:
            return (
                DatasetRevision.objects.filter(id=revision_id, dataset__owner=request.user).exists()
                or PendingContentUpdate.objects.filter(id=revision_id, dataset__owner=request.user).exists()
            )
        return False


class IsDatasetOwnerOrContributor(BasePermission):
    def has_permission(self, request, view):
        from .models import Dataset, Contributor
        dataset_id = view.kwargs.get("dataset_id")
        if not dataset_id:
            return False
        if Dataset.objects.filter(id=dataset_id, owner=request.user).exists():
            return True

        profile = getattr(request.user, "profile", None)
        if not profile or profile.role not in ("researcher", "admin"):
            return False
        return Contributor.objects.filter(dataset_id=dataset_id, user=request.user).exists()
class IsDatasetOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        dataset = obj if hasattr(obj, "owner") else obj.dataset
        return dataset.owner_id == request.user.id