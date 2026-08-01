from rest_framework.permissions import BasePermission


class IsDatasetOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        dataset = obj if hasattr(obj, "owner") else obj.dataset
        return dataset.owner_id == request.user.id