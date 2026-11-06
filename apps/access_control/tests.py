from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.datasets.models import Dataset
from .models import AccessRequest, AccessRequestPermission, Group, GroupMembership, GroupPermissionGrant, Permission, PermissionAuditLog, UserPermissionGrant
from .services import effective_permissions, grant_to_user, has_permission, review_request, revoke_user_grant


class PermissionSystemTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.manager = User.objects.create_user(username="manager_v2", email="manager@aastu.edu.et", password="test")
        cls.users = [User.objects.create_user(username=f"member_v2_{i}", email=f"member{i}@aastu.edu.et", password="test") for i in range(15)]
        cls.datasets = [Dataset.objects.create(title=f"Dataset {i}", owner=cls.manager) for i in range(5)]
        cls.groups = [Group.objects.create(name=f"Group {i}", created_by=cls.manager) for i in range(3)]
        for code in ["user.view", "user.manage", "user.permission.grant", "user.permission.revoke", "access_request.review", "group.create", "group.member.manage", "group.permission.manage"]:
            UserPermissionGrant.objects.create(user=cls.manager, permission_id=code, resource_type="platform", granted_by=cls.manager)

    def setUp(self):
        self.client = APIClient()

    def test_resource_discovery_limits_private_and_unpublished_datasets(self):
        published = self.datasets[0]
        published.status = Dataset.Status.PUBLISHED
        published.save(update_fields=["status"])
        private = self.datasets[1]
        private.status = Dataset.Status.PUBLISHED
        private.visibility = Dataset.Visibility.PRIVATE
        private.save(update_fields=["status", "visibility"])

        self.client.force_authenticate(self.users[0])
        response = self.client.get("/api/access-control/resources/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.data}, {published.id})

        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/access-control/resources/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["id"] for row in response.data}, {d.id for d in self.datasets})

    def test_multiple_groups_and_direct_grant_are_independent(self):
        target = self.users[0]
        GroupMembership.objects.create(group=self.groups[0], user=target)
        GroupMembership.objects.create(group=self.groups[1], user=target)
        GroupMembership.objects.create(group=self.groups[0], user=self.users[1])
        for code in ["dataset.view", "dataset.edit"]:
            GroupPermissionGrant.objects.create(group=self.groups[0], permission_id=code, resource_type="dataset", resource_id=self.datasets[0].id, granted_by=self.manager)
        GroupPermissionGrant.objects.create(group=self.groups[1], permission_id="dataset.download", resource_type="dataset", resource_id=self.datasets[0].id, granted_by=self.manager)
        direct = grant_to_user(self.manager, target, Permission.objects.get(pk="dataset.delete"), "dataset", self.datasets[0].id)
        self.assertEqual({p["code"] for p in effective_permissions(target, "dataset", self.datasets[0].id)}, {"dataset.view", "dataset.edit", "dataset.download", "dataset.delete"})
        self.assertFalse(has_permission(self.users[1], "dataset.delete", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.delete", "dataset", self.datasets[1].id))
        revoke_user_grant(self.manager, direct)
        self.assertFalse(has_permission(target, "dataset.delete", "dataset", self.datasets[0].id))
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        GroupMembership.objects.filter(group=self.groups[1], user=target).delete()
        self.assertFalse(has_permission(target, "dataset.download", "dataset", self.datasets[0].id))
        self.assertTrue(has_permission(target, "dataset.edit", "dataset", self.datasets[0].id))
        self.assertTrue(PermissionAuditLog.objects.filter(action="user.revoke", target_user=target).exists())

    def test_expiration_and_archival_remove_effective_access(self):
        target = self.users[0]
        UserPermissionGrant.objects.create(user=target, permission_id="dataset.view", resource_type="dataset", resource_id=self.datasets[0].id, expires_at=timezone.now() - timedelta(days=1))
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        GroupMembership.objects.create(group=self.groups[0], user=target)
        GroupPermissionGrant.objects.create(group=self.groups[0], permission_id="dataset.view", resource_type="dataset", resource_id=self.datasets[0].id)
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        self.groups[0].archived_at = timezone.now()
        self.groups[0].save()
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))

    def test_partial_approval_only_grants_selected_permissions(self):
        target = self.users[0]
        req = AccessRequest.objects.create(requester=target, resource_type="dataset", resource_id=self.datasets[0].id, justification="Need access for a project")
        for code in ["dataset.view", "dataset.edit", "dataset.delete"]:
            AccessRequestPermission.objects.create(request=req, permission_id=code)
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        result = review_request(self.manager, req, {
            "dataset.view": {"decision": "approved"},
            "dataset.edit": {"decision": "approved"},
            "dataset.delete": {"decision": "rejected", "reason": "Not needed"},
        }, "Approved limited access")
        self.assertEqual(result.status, "partially_approved")
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        self.assertTrue(has_permission(target, "dataset.edit", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.delete", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[1].id))

    def test_requester_and_unauthorized_user_cannot_review(self):
        req = AccessRequest.objects.create(requester=self.users[0], resource_type="dataset", resource_id=self.datasets[0].id, justification="Need access for a project")
        AccessRequestPermission.objects.create(request=req, permission_id="dataset.view")
        with self.assertRaises(PermissionError):
            review_request(self.users[0], req, {"dataset.view": {"decision": "approved"}})
        with self.assertRaises(PermissionError):
            review_request(self.users[1], req, {"dataset.view": {"decision": "approved"}})
        self.assertFalse(UserPermissionGrant.objects.filter(user=self.users[0], permission_id="dataset.view").exists())

    def test_api_denies_grants_without_explicit_authority(self):
        self.client.force_authenticate(self.users[0])
        response = self.client.post(f"/api/access-control/users/{self.users[1].id}/grants/", {
            "permission": "dataset.view", "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(UserPermissionGrant.objects.filter(user=self.users[1], permission_id="dataset.view").exists())

    def test_api_request_review_and_audit(self):
        self.datasets[0].status = Dataset.Status.PUBLISHED
        self.datasets[0].save(update_fields=["status"])
        self.client.force_authenticate(self.users[0])
        response = self.client.post("/api/access-control/requests/", {
            "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
            "permissions": ["dataset.view", "dataset.download"],
            "justification": "Need these data for my research project",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(self.manager)
        review = self.client.post(f"/api/access-control/requests/{response.data['id']}/review/", {
            "decisions": [{"permission": "dataset.view", "decision": "approved"},
                          {"permission": "dataset.download", "decision": "rejected", "reason": "Restricted"}],
            "comment": "Limited access approved",
        }, format="json")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.data["status"], "partially_approved")
        self.assertTrue(PermissionAuditLog.objects.filter(action="request.review").exists())

    def test_matrix_edits_are_scoped_and_audited(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(f"/api/access-control/resources/dataset/{self.datasets[0].id}/matrix/", {
            "edits": [
                {"type": "user", "id": self.users[0].id, "permission": "dataset.view", "selected": True},
                {"type": "group", "id": str(self.groups[0].id), "permission": "dataset.download", "selected": True},
            ],
        }, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(has_permission(self.users[0], "dataset.view", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(self.users[0], "dataset.view", "dataset", self.datasets[1].id))
        self.assertEqual(PermissionAuditLog.objects.filter(action__in=["user.grant", "group.grant"]).count(), 2)

    def test_matrix_invalid_change_does_not_grant_anything(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post(f"/api/access-control/resources/dataset/{self.datasets[0].id}/matrix/", {
            "edits": [
                {"type": "user", "id": self.users[0].id, "permission": "dataset.view", "selected": True},
                {"type": "group", "id": "invalid", "permission": "dataset.download", "selected": True},
            ],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(has_permission(self.users[0], "dataset.view", "dataset", self.datasets[0].id))

    def test_private_dataset_cannot_be_requested_through_public_flow(self):
        self.datasets[0].status = Dataset.Status.PUBLISHED
        self.datasets[0].visibility = Dataset.Visibility.PRIVATE
        self.datasets[0].save(update_fields=["status", "visibility"])
        self.client.force_authenticate(self.users[0])
        response = self.client.post("/api/access-control/requests/", {
            "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
            "permissions": ["dataset.view"], "justification": "Need access for research",
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AccessRequest.objects.filter(requester=self.users[0]).exists())

    def test_renewal_updates_existing_grant_without_duplicate(self):
        target = self.users[0]
        old_expiry = timezone.now() + timedelta(days=1)
        grant = grant_to_user(self.manager, target, Permission.objects.get(pk="dataset.view"), "dataset", self.datasets[0].id, old_expiry)
        req = AccessRequest.objects.create(requester=target, resource_type="dataset", resource_id=self.datasets[0].id, justification="Renewing access for project")
        AccessRequestPermission.objects.create(request=req, permission_id="dataset.view")
        new_expiry = timezone.now() + timedelta(days=90)
        review_request(self.manager, req, {"dataset.view": {"decision": "approved", "expires_at": new_expiry}})
        grant.refresh_from_db()
        self.assertEqual(grant.expires_at, new_expiry)
        self.assertEqual(UserPermissionGrant.objects.filter(user=target, permission_id="dataset.view").count(), 1)

    def test_expired_request_cannot_be_approved(self):
        req = AccessRequest.objects.create(requester=self.users[0], resource_type="dataset", resource_id=self.datasets[0].id,
                                           justification="Need access for research", expires_at=timezone.now() - timedelta(seconds=1))
        AccessRequestPermission.objects.create(request=req, permission_id="dataset.view")
        with self.assertRaises(ValueError):
            review_request(self.manager, req, {"dataset.view": {"decision": "approved"}})
        self.assertFalse(has_permission(self.users[0], "dataset.view", "dataset", self.datasets[0].id))

    def test_scoped_download_grant_reaches_existing_download_endpoint(self):
        dataset = self.datasets[0]
        dataset.visibility = Dataset.Visibility.PRIVATE
        dataset.save(update_fields=["visibility"])
        self.client.force_authenticate(self.users[0])
        with patch("apps.sharing.views.user_meets_access_requirements", return_value=True):
            denied = self.client.get(f"/api/sharing/{dataset.id}/download/")
            self.assertEqual(denied.status_code, 403)
            grant_to_user(self.manager, self.users[0], Permission.objects.get(pk="dataset.download"), "dataset", dataset.id)
            allowed = self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.assertEqual(allowed.status_code, 404)
        self.assertEqual(allowed.data["detail"], "This dataset has no published file yet.")
