from rest_framework.permissions import BasePermission
from .models import Contributor, Dataset, PendingContentUpdate, RevisionRequest


class IsDatasetOwner(BasePermission):
    def has_permission(self, request, view):
        dataset_id = view.kwargs.get("dataset_id")
        if dataset_id:
            dataset = Dataset.objects.filter(id=dataset_id).first()
            if dataset and dataset.is_owned_by(request.user):
                return True
            self.message = "Only the dataset owner can do this."
            return False

        update_id = view.kwargs.get("update_id")
        if update_id:
            update = PendingContentUpdate.objects.filter(id=update_id).first()
            if update and update.dataset.is_owned_by(request.user):
                return True
            self.message = "Only the dataset owner can do this."
            return False

        request_id = view.kwargs.get("request_id")
        if request_id:
            revision_request = RevisionRequest.objects.filter(id=request_id).first()
            if revision_request and revision_request.dataset.is_owned_by(request.user):
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
    """Owner, or a co-author whose `permission` is EDIT. A view-only
    co-author, or anyone with `contributor_type=CONTRIBUTOR` (earned only
    through an approved revision, never invited with edit rights), is NOT
    covered here — they must go through the revision flow instead."""
    def has_permission(self, request, view):

        dataset_id = view.kwargs.get("dataset_id")

        if not dataset_id:
            return False

        if Dataset.objects.filter(id=dataset_id, owner=request.user).exists():
            return True

        contributor = Contributor.objects.filter(dataset_id=dataset_id, user=request.user).first()

        if contributor and contributor.can_edit():
            return True

        self.message = "You don't have direct edit access to this dataset. Submit a revision request instead."
        return False
