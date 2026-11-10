from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.datasets.models import Dataset
from apps.accounts.models import ActivityLog, UserRole
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
        for code in [
            "user.view", "user.manage", "user.permission.grant", "user.permission.revoke",
            "access_request.create", "access_request.view", "access_request.review",
            "access_request.approve", "access_request.reject",
            "group.create", "group.member.manage", "group.permission.manage",
            "audit.view", "audit.export", "dataset.create", "dataset.approve", "dataset.publish",
            "project.create", "collection.create",
        ]:
            UserPermissionGrant.objects.create(user=cls.manager, permission_id=code, resource_type="platform", granted_by=cls.manager)

    def setUp(self):
        self.client = APIClient()

    def test_user_management_audit_is_scoped_to_affected_user(self):
        target, other = self.users[:2]
        PermissionAuditLog.objects.create(actor=self.manager, target_user=target, action="user.grant")
        PermissionAuditLog.objects.create(actor=self.manager, target_user=other, action="user.revoke")
        ActivityLog.log(self.manager, "role_granted", str(target.id), extra={"role": "reviewer"})
        ActivityLog.log(self.manager, "user_deactivated", str(other.id))

        self.client.force_authenticate(target)
        response = self.client.get("/api/access-control/audit/user-management/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row["action"] for row in response.data}, {"user.grant", "role_granted"})

        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/access-control/audit/user-management/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)

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

    def test_bulk_user_grants_are_atomic_and_scoped(self):
        target = self.users[0]
        self.client.force_authenticate(self.manager)
        url = f"/api/access-control/users/{target.id}/grants/bulk/"
        rows = [
            {"permission": "dataset.view", "resource_type": "dataset", "resource_id": str(self.datasets[0].id)},
            {"permission": "dataset.edit", "resource_type": "dataset", "resource_id": str(self.datasets[0].id)},
        ]
        response = self.client.post(url, {"grants": rows}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data), 2)
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        self.assertTrue(has_permission(target, "dataset.edit", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.edit", "dataset", self.datasets[1].id))

        invalid = self.client.post(url, {"grants": [
            {"permission": "dataset.delete", "resource_type": "dataset", "resource_id": str(self.datasets[0].id)},
            rows[0],
        ]}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(has_permission(target, "dataset.delete", "dataset", self.datasets[0].id))

        unsupported = self.client.post(url, {"grants": [{
            "permission": "dataset.approve", "resource_type": "dataset",
            "resource_id": str(self.datasets[0].id),
        }]}, format="json")
        self.assertEqual(unsupported.status_code, 400)
        self.assertFalse(has_permission(target, "dataset.approve", "dataset", self.datasets[0].id))

        self.client.force_authenticate(self.users[1])
        denied = self.client.post(url, {"grants": [rows[0]]}, format="json")
        self.assertEqual(denied.status_code, 403)

    def test_catalog_marks_only_endpoint_enforced_permissions_as_grantable(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/access-control/catalog/")
        self.assertEqual(response.status_code, 200)
        by_code = {row["code"]: row for row in response.data}
        self.assertTrue(by_code["dataset.edit"]["enforced"])
        self.assertTrue(by_code["user.view"]["enforced"])
        self.assertFalse(by_code["dataset.approve"]["enforced"])

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
        self.manager.is_staff = True
        self.manager.save(update_fields=["is_staff"])
        audit_events = self.client.get("/api/access-control/audit/user-management/")
        self.assertEqual(audit_events.status_code, 200)
        review_event = next(row for row in audit_events.data if row["action"] == "request.review")
        self.assertEqual(review_event["detail"], "partially approved")
        self.assertEqual(review_event["target"], self.users[0].email)

    @patch("apps.access_control.views.notify")
    def test_access_request_notifies_admin_and_appears_for_review(self, send_notification):
        UserRole.objects.create(profile=self.manager.profile, role=UserRole.RoleChoice.ADMIN)
        target = self.users[0]
        self.datasets[0].status = Dataset.Status.PUBLISHED
        self.datasets[0].save(update_fields=["status"])
        self.client.force_authenticate(target)
        created = self.client.post("/api/access-control/requests/", {
            "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
            "permissions": ["dataset.view"], "justification": "I need to inspect this dataset",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        send_notification.assert_called_once()
        self.assertEqual(send_notification.call_args.args[0], self.manager)
        self.assertEqual(send_notification.call_args.kwargs["link_path"],
                         f"/access-management/requests/{created.data['id']}")

        self.client.force_authenticate(self.manager)
        listed = self.client.get("/api/access-control/requests/?status=pending")
        self.assertEqual(listed.status_code, 200)
        self.assertIn(created.data["id"], {row["id"] for row in listed.data})

    @patch("apps.access_control.views.notify")
    def test_group_membership_request_notifies_admin(self, send_notification):
        UserRole.objects.create(profile=self.manager.profile, role=UserRole.RoleChoice.ADMIN)
        self.client.force_authenticate(self.users[0])
        created = self.client.post("/api/access-control/requests/", {
            "kind": "group_membership", "group": str(self.groups[0].id),
            "resource_type": "platform", "justification": "I need to join this research group",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        send_notification.assert_called_once()
        self.assertIn("group membership", send_notification.call_args.args[2])

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

    def test_scoped_view_edit_and_delete_reach_dataset_endpoints(self):
        dataset = self.datasets[0]
        dataset.visibility = Dataset.Visibility.PRIVATE
        dataset.status = Dataset.Status.PUBLISHED
        dataset.save(update_fields=["visibility", "status"])
        target = self.users[0]
        self.client.force_authenticate(target)
        detail_url = f"/api/datasets/{dataset.id}/"
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        grant_to_user(self.manager, target, Permission.objects.get(pk="dataset.view"), "dataset", dataset.id)
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        resources = self.client.get("/api/access-control/my-resources/")
        self.assertEqual(resources.status_code, 200)
        self.assertEqual(resources.data[0]["permissions"], ["dataset.view"])
        self.assertEqual(str(resources.data[0]["id"]), str(dataset.id))
        self.assertEqual(self.client.get(f"/api/datasets/{self.datasets[1].id}/").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/datasets/{dataset.id}/delete/").status_code, 403)

        grant_to_user(self.manager, target, Permission.objects.get(pk="dataset.edit"), "dataset", dataset.id)
        edited = self.client.patch(f"/api/datasets/{dataset.id}/update/", {"title": "Granted title"}, format="json")
        self.assertEqual(edited.status_code, 200)
        dataset.refresh_from_db()
        self.assertEqual(dataset.title, "Granted title")
        self.assertEqual(self.client.patch(f"/api/datasets/{dataset.id}/update/", {"visibility": "public"}, format="json").status_code, 403)
        self.assertEqual(self.client.patch(f"/api/datasets/{self.datasets[1].id}/update/", {"title": "Wrong dataset"}, format="json").status_code, 403)

        grant_to_user(self.manager, target, Permission.objects.get(pk="dataset.delete"), "dataset", dataset.id)
        self.assertEqual(self.client.delete(f"/api/datasets/{self.datasets[1].id}/delete/").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/datasets/{dataset.id}/delete/").status_code, 204)
        self.assertEqual(self.client.get("/api/access-control/my-resources/").data, [])

    def test_group_grant_updates_my_resources_after_membership_removed(self):
        target = self.users[0]
        group = self.groups[0]
        GroupMembership.objects.create(group=group, user=target)
        GroupPermissionGrant.objects.create(
            group=group, permission_id="dataset.view", resource_type="dataset",
            resource_id=self.datasets[0].id, granted_by=self.manager,
        )
        self.client.force_authenticate(target)
        self.assertEqual(len(self.client.get("/api/access-control/my-resources/").data), 1)
        GroupMembership.objects.filter(group=group, user=target).delete()
        self.assertEqual(self.client.get("/api/access-control/my-resources/").data, [])

    def test_group_membership_request_requires_separate_reviewer_and_grants_inheritance(self):
        group = self.groups[0]
        GroupPermissionGrant.objects.create(
            group=group, permission_id="dataset.view", resource_type="dataset",
            resource_id=self.datasets[0].id, granted_by=self.manager,
        )
        target = self.users[0]
        self.client.force_authenticate(target)
        directory = self.client.get("/api/access-control/group-directory/")
        self.assertEqual(directory.status_code, 200)
        self.assertIn(group.id, {row["id"] for row in directory.data})
        response = self.client.post("/api/access-control/requests/", {
            "kind": "group_membership", "group": str(group.id),
            "resource_type": "platform", "justification": "Join the research team",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["kind"], "group_membership")
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))

        UserPermissionGrant.objects.create(user=target, permission_id="access_request.review",
            resource_type="platform", granted_by=self.manager)
        denied = self.client.post(f"/api/access-control/requests/{response.data['id']}/review/", {
            "decision": "approved",
        }, format="json")
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(GroupMembership.objects.filter(group=group, user=target).exists())

        self.client.force_authenticate(self.manager)
        reviewed = self.client.post(f"/api/access-control/requests/{response.data['id']}/review/", {
            "decision": "approved", "comment": "Welcome",
        }, format="json")
        self.assertEqual(reviewed.status_code, 200)
        self.assertTrue(GroupMembership.objects.filter(group=group, user=target).exists())
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        self.assertFalse(UserPermissionGrant.objects.filter(user=target, permission_id="dataset.view").exists())
        self.manager.is_staff = True
        self.manager.save(update_fields=["is_staff"])
        events = self.client.get("/api/access-control/audit/user-management/")
        self.assertEqual(events.status_code, 200)
        self.assertTrue(any(row["action"] == "request.review" and row["detail"] == "approved"
                            and row["target"] == target.email for row in events.data))

    def test_group_permission_request_approves_only_selected_actions(self):
        requester = self.users[0]
        group = self.groups[0]
        GroupMembership.objects.create(group=group, user=requester)
        UserPermissionGrant.objects.create(user=requester, permission_id="group.permission.manage",
            resource_type="platform", granted_by=self.manager)
        self.datasets[0].status = Dataset.Status.PUBLISHED
        self.datasets[0].save(update_fields=["status"])
        self.client.force_authenticate(self.users[1])
        denied = self.client.post("/api/access-control/requests/", {
            "kind": "group_permission", "group": str(group.id),
            "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
            "permissions": ["dataset.view"], "justification": "Need access for our research group",
        }, format="json")
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(requester)
        response = self.client.post("/api/access-control/requests/", {
            "kind": "group_permission", "group": str(group.id),
            "resource_type": "dataset", "resource_id": str(self.datasets[0].id),
            "permissions": ["dataset.view", "dataset.edit"],
            "justification": "Need shared access for our research team",
        }, format="json")
        self.assertEqual(response.status_code, 201)
        self.client.force_authenticate(self.manager)
        reviewed = self.client.post(f"/api/access-control/requests/{response.data['id']}/review/", {
            "decisions": [
                {"permission": "dataset.view", "decision": "approved"},
                {"permission": "dataset.edit", "decision": "rejected", "reason": "Not needed"},
            ],
        }, format="json")
        self.assertEqual(reviewed.status_code, 200)
        self.assertEqual(reviewed.data["status"], "partially_approved")
        self.assertTrue(has_permission(requester, "dataset.view", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(requester, "dataset.edit", "dataset", self.datasets[0].id))
        self.assertFalse(UserPermissionGrant.objects.filter(user=requester, permission_id="dataset.view").exists())

    def test_group_creation_adds_multiple_members_and_scoped_grants_atomically(self):
        self.client.force_authenticate(self.manager)
        response = self.client.post("/api/access-control/groups/", {
            "name": "Research Team V2", "description": "Shared dataset access",
            "members": [self.users[0].id, self.users[1].id],
            "grants": [{"permission": "dataset.view", "resource_type": "dataset",
                        "resource_id": str(self.datasets[0].id)}],
        }, format="json")
        self.assertEqual(response.status_code, 201)
        group = Group.objects.get(pk=response.data["id"])
        self.assertEqual(group.members.count(), 2)
        for user in self.users[:2]:
            self.assertTrue(has_permission(user, "dataset.view", "dataset", self.datasets[0].id))
            self.assertFalse(has_permission(user, "dataset.view", "dataset", self.datasets[1].id))

        invalid = self.client.post("/api/access-control/groups/", {
            "name": "Invalid Team", "members": [self.users[0].id],
            "grants": [{"permission": "dataset.view", "resource_type": "dataset",
                        "resource_id": str(self.groups[0].id)}],
        }, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(Group.objects.filter(name="Invalid Team").exists())

    def test_group_creation_cannot_add_members_without_explicit_authority(self):
        creator = self.users[0]
        UserPermissionGrant.objects.create(user=creator, permission_id="group.create",
            resource_type="platform", granted_by=self.manager)
        self.client.force_authenticate(creator)
        denied = self.client.post("/api/access-control/groups/", {
            "name": "Unauthorized Team", "members": [self.users[1].id],
        }, format="json")
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(Group.objects.filter(name="Unauthorized Team").exists())

    def test_last_permission_manager_is_protected_when_group_access_changes(self):
        group = self.groups[0]
        target = self.users[0]
        membership = GroupMembership.objects.create(group=group, user=target)
        group_grant = GroupPermissionGrant.objects.create(
            group=group, permission_id="user.permission.grant", resource_type="platform",
            granted_by=self.manager,
        )
        direct = UserPermissionGrant.objects.get(user=self.manager, permission_id="user.permission.grant")
        direct.revoked_at = timezone.now()
        direct.save(update_fields=["revoked_at"])
        UserPermissionGrant.objects.create(
            user=self.manager, permission_id="group.delete", resource_type="platform",
            granted_by=self.manager,
        )
        self.client.force_authenticate(self.manager)

        grant_url = f"/api/access-control/group-grants/{group_grant.id}/"
        member_url = f"/api/access-control/groups/{group.id}/members/{target.id}/"
        group_url = f"/api/access-control/groups/{group.id}/"
        for method, url in ((self.client.delete, grant_url), (self.client.delete, member_url),
                            (self.client.delete, group_url)):
            response = method(url)
            self.assertEqual(response.status_code, 400)
            self.assertIn("last permission manager", response.data["detail"])
        self.assertTrue(has_permission(target, "user.permission.grant"))

        UserPermissionGrant.objects.create(
            user=self.users[1], permission_id="user.permission.grant",
            resource_type="platform", granted_by=self.manager,
        )
        self.assertEqual(self.client.delete(grant_url).status_code, 204)
        self.assertFalse(has_permission(target, "user.permission.grant"))
        self.assertTrue(GroupMembership.objects.filter(pk=membership.pk).exists())

    def test_new_catalog_codes_are_marked_enforced(self):
        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/access-control/catalog/")
        self.assertEqual(response.status_code, 200)
        by_code = {row["code"]: row["enforced"] for row in response.data}
        for code in (
            "dataset.create", "dataset.upload", "dataset.metadata.edit", "dataset.version.view",
            "dataset.version.create", "dataset.version.restore", "dataset.approve", "dataset.publish",
            "user.manage", "access_request.approve", "audit.export", "project.view", "collection.view",
            "dataset_file.download",
        ):
            self.assertTrue(by_code.get(code), code)

    def test_dataset_create_grant_allows_upload_permission_class(self):
        from unittest.mock import patch

        from apps.accounts.models import UserProfile
        from apps.accounts.permissions import CanUploadDatasets
        from rest_framework.test import APIRequestFactory

        target = self.users[0]
        profile, _ = UserProfile.objects.get_or_create(user=target)
        profile.can_upload_datasets = False
        profile.save(update_fields=["can_upload_datasets"])
        factory = APIRequestFactory()
        request = factory.get("/")
        request.user = target
        with patch.object(UserProfile, "is_profile_complete", return_value=True):
            self.assertFalse(CanUploadDatasets().has_permission(request, None))
            UserPermissionGrant.objects.create(
                user=target, permission_id="dataset.create", resource_type="platform", granted_by=self.manager,
            )
            self.assertTrue(CanUploadDatasets().has_permission(request, None))

    def test_project_view_inherits_to_linked_dataset_view(self):
        from apps.datasets.models import ResearchProject

        target = self.users[0]
        project = ResearchProject.objects.create(name="Vision Lab", created_by=self.manager)
        project.datasets.add(self.datasets[0])
        UserPermissionGrant.objects.create(
            user=target, permission_id="project.view", resource_type="project",
            resource_id=project.id, granted_by=self.manager,
        )
        self.assertTrue(has_permission(target, "dataset.view", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.edit", "dataset", self.datasets[0].id))
        self.assertFalse(has_permission(target, "dataset.view", "dataset", self.datasets[1].id))

    def test_audit_export_requires_export_permission(self):
        self.client.force_authenticate(self.users[0])
        self.assertEqual(self.client.get("/api/access-control/audit/export/").status_code, 403)
        self.client.force_authenticate(self.manager)
        response = self.client.get("/api/access-control/audit/export/")
        self.assertEqual(response.status_code, 200)

    def test_legacy_share_review_endpoints_are_gone(self):
        self.client.force_authenticate(self.manager)
        for path in (
            "/api/sharing/access-requests/queue/",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 410)

    def test_admin_permission_class_uses_user_manage_not_role(self):
        from apps.accounts.permissions import IsAdminOnly
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.get("/")
        request.user = self.users[0]
        self.assertFalse(IsAdminOnly().has_permission(request, None))
        UserPermissionGrant.objects.create(
            user=self.users[0], permission_id="user.manage", resource_type="platform", granted_by=self.manager,
        )
        self.assertTrue(IsAdminOnly().has_permission(request, None))
