from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import BlockedCredential, UserRole
from apps.datasets.factories import make_user
from apps.datasets.models import Dataset

User = get_user_model()


class AdminLifecycleControlsTests(APITestCase):
    def setUp(self):
        self.admin = make_user("lifeadmin", "lifeadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(self.admin)

    def test_inactive_user_can_be_permanently_deleted_and_blocked(self):
        target = make_user("inactiveuser", "inactiveuser@aastu.edu.et", role="researcher")
        target.is_active = False
        target.save(update_fields=["is_active"])

        resp = self.client.delete(f"/api/admin-panel/users/{target.id}/delete-inactive/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(User.objects.filter(id=target.id).exists())
        self.assertTrue(BlockedCredential.is_email_blocked("inactiveuser@aastu.edu.et"))
        self.assertTrue(BlockedCredential.is_username_blocked("inactiveuser"))

    def test_active_user_cannot_be_permanently_deleted(self):
        target = make_user("activeuser", "activeuser@aastu.edu.et", role="researcher")

        resp = self.client.delete(f"/api/admin-panel/users/{target.id}/delete-inactive/")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.filter(id=target.id).exists())

    def test_draft_expiration_deletes_only_old_active_drafts(self):
        owner = make_user("draftowner", "draftowner@aastu.edu.et", role="researcher")
        old_draft = Dataset.objects.create(title="Old Draft", owner=owner, status=Dataset.Status.DRAFT)
        new_draft = Dataset.objects.create(title="New Draft", owner=owner, status=Dataset.Status.DRAFT)
        Dataset.objects.filter(id=old_draft.id).update(updated_at=timezone.now() - timedelta(days=45))

        preview = self.client.get("/api/admin-panel/draft-expiration/?days=30")
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(preview.data["expired_count"], 1)

        resp = self.client.post("/api/admin-panel/draft-expiration/", {"days": 30})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["deleted_count"], 1)
        self.assertFalse(Dataset.objects.filter(id=old_draft.id).exists())
        self.assertTrue(Dataset.objects.filter(id=new_draft.id).exists())

    def test_admin_succession_grants_new_admin_and_blocks_previous_credentials(self):
        previous = make_user("oldadmin", "oldadmin@aastu.edu.et", role="admin")

        resp = self.client.post("/api/admin-panel/admin-succession/", {
            "previous_admin_id": previous.id,
            "email": "newadmin@aastu.edu.et",
            "full_name": "New Admin",
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        previous.refresh_from_db()
        new_admin = User.objects.get(email="newadmin@aastu.edu.et")
        self.assertFalse(previous.is_active)
        self.assertFalse(previous.profile.roles.filter(role=UserRole.RoleChoice.ADMIN).exists())
        self.assertTrue(new_admin.profile.roles.filter(role=UserRole.RoleChoice.ADMIN).exists())
        self.assertTrue(BlockedCredential.is_email_blocked("oldadmin@aastu.edu.et"))
        self.assertTrue(BlockedCredential.is_username_blocked("oldadmin"))
