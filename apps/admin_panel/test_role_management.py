from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.accounts.models import UserRole
from apps.admin_panel.models import DatasetReviewerAssignment, ModerationDecision


class AdminCreateUserMultiRoleTests(APITestCase):
    def test_new_email_creates_account_with_primary_role(self):
        admin = make_user("mracadmin1", "mracadmin1@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)

        resp = self.client.post("/api/admin-panel/users/create/", {
            "email": "brandnew@aastu.edu.et", "full_name": "Brand New", "role": "reviewer",
        })
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        role = UserRole.objects.get(profile__user__email="brandnew@aastu.edu.et", role="reviewer")
        self.assertTrue(role.is_primary)

    def test_existing_email_grants_role_instead_of_erroring(self):
        admin = make_user("mracadmin2", "mracadmin2@aastu.edu.et", role="admin")
        target = make_user("mractarget2", "mractarget2@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(admin)

        resp = self.client.post("/api/admin-panel/users/create/", {
            "email": "mractarget2@aastu.edu.et", "full_name": "Irrelevant Here", "role": "admin",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "role_granted")

        roles = set(target.profile.roles.values_list("role", flat=True))
        self.assertEqual(roles, {"public", "reviewer", "admin"})
        self.assertEqual(
            UserRole.objects.filter(profile__user__email="mractarget2@aastu.edu.et").count(),
            len(roles),
        )

    def test_existing_email_duplicate_role_rejected(self):
        admin = make_user("mracadmin3", "mracadmin3@aastu.edu.et", role="admin")
        make_user("mractarget3", "mractarget3@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(admin)

        resp = self.client.post("/api/admin-panel/users/create/", {
            "email": "mractarget3@aastu.edu.et", "full_name": "x", "role": "reviewer",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class AdminGrantRoleTests(APITestCase):
    def test_grant_role_rejects_duplicate(self):
        admin = make_user("mrgadmin1", "mrgadmin1@aastu.edu.et", role="admin")
        target = make_user("mrgtarget1", "mrgtarget1@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(admin)

        resp = self.client.post(f"/api/admin-panel/users/{target.id}/grant-role/", {"role": "reviewer"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_grant_role_second_role_is_not_primary(self):
        admin = make_user("mrgadmin2", "mrgadmin2@aastu.edu.et", role="admin")
        target = make_user("mrgtarget2", "mrgtarget2@aastu.edu.et", role="reviewer")
        UserRole.objects.filter(profile=target.profile, role="reviewer").update(is_primary=True)
        self.client.force_authenticate(admin)

        self.client.post(f"/api/admin-panel/users/{target.id}/grant-role/", {"role": "admin"})
        self.assertTrue(UserRole.objects.get(profile=target.profile, role="reviewer").is_primary)
        self.assertFalse(UserRole.objects.get(profile=target.profile, role="admin").is_primary)


class AdminRevokeRoleGuardTests(APITestCase):
    def test_cannot_revoke_public(self):
        admin = make_user("mrvadmin1", "mrvadmin1@aastu.edu.et", role="admin")
        target = make_user("mrvtarget1", "mrvtarget1@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(admin)

        resp = self.client.post(f"/api/admin-panel/users/{target.id}/revoke-role/", {"role": "public"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_revoke_own_admin(self):
        admin = make_user("mrvadmin2", "mrvadmin2@aastu.edu.et", role="admin")
        make_user("mrvother2", "mrvother2@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)

        resp = self.client.post(f"/api/admin-panel/users/{admin.id}/revoke-role/", {"role": "admin"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_last_admin_cannot_be_revoked_by_another_admin(self):
        sole_admin = make_user("mrvsole4", "mrvsole4@aastu.edu.et", role="admin")
        acting_admin = make_user("mrvacting4", "mrvacting4@aastu.edu.et", role="admin")

        self.client.force_authenticate(acting_admin)
        resp = self.client.post(f"/api/admin-panel/users/{sole_admin.id}/revoke-role/", {"role": "admin"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        resp2 = self.client.post(f"/api/admin-panel/users/{acting_admin.id}/revoke-role/", {"role": "admin"})
        self.assertEqual(resp2.status_code, status.HTTP_400_BAD_REQUEST)


class AdminRevokeRolePrimaryReassignmentTests(APITestCase):
    def test_revoking_primary_role_promotes_next_oldest(self):
        admin = make_user("mrpadmin1", "mrpadmin1@aastu.edu.et", role="admin")
        target = make_user("mrptarget1", "mrptarget1@aastu.edu.et", role="reviewer")
        UserRole.objects.filter(profile=target.profile, role="reviewer").update(is_primary=True)
        self.client.force_authenticate(admin)

        self.client.post(f"/api/admin-panel/users/{target.id}/grant-role/", {"role": "admin"})
        self.assertFalse(UserRole.objects.get(profile=target.profile, role="admin").is_primary)

        resp = self.client.post(f"/api/admin-panel/users/{target.id}/revoke-role/", {"role": "reviewer"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["primary_role"], "admin")
        self.assertTrue(UserRole.objects.get(profile=target.profile, role="admin").is_primary)


class AdminRevokeReviewerCleanupTests(APITestCase):
    def _make_pending_dataset(self, owner_suffix="owner"):
        owner = make_user(f"mrc{owner_suffix}", f"mrc{owner_suffix}@aastu.edu.et", role="researcher")
        return Dataset.objects.create(title="Cleanup DS", owner=owner, status=Dataset.Status.PENDING)

    def test_revoking_unvoted_reviewer_releases_and_tops_up(self):
        admin = make_user("mrcadmin1", "mrcadmin1@aastu.edu.et", role="admin")
        dataset = self._make_pending_dataset("own1")

        r1 = make_user("mrcrev1a", "mrcrev1a@aastu.edu.et", role="reviewer")
        r2 = make_user("mrcrev1b", "mrcrev1b@aastu.edu.et", role="reviewer")
        r3 = make_user("mrcrev1c", "mrcrev1c@aastu.edu.et", role="reviewer")
        spare = make_user("mrcrev1spare", "mrcrev1spare@aastu.edu.et", role="reviewer")

        for r in (r1, r2, r3):
            DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=r)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/users/{r1.id}/revoke-role/", {"role": "reviewer"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertFalse(
            DatasetReviewerAssignment.objects.filter(dataset=dataset, reviewer=r1).exists()
        )
        self.assertEqual(str(dataset.id), resp.data["released_datasets"][0])
        self.assertTrue(
            DatasetReviewerAssignment.objects.filter(dataset=dataset, reviewer=spare).exists()
        )
        self.assertEqual(
            DatasetReviewerAssignment.objects.filter(dataset=dataset).count(), 3
        )

    def test_revoking_reviewer_who_already_voted_preserves_vote(self):
        admin = make_user("mrcadmin2", "mrcadmin2@aastu.edu.et", role="admin")
        dataset = self._make_pending_dataset("own2")

        r1 = make_user("mrcrev2a", "mrcrev2a@aastu.edu.et", role="reviewer")
        r2 = make_user("mrcrev2b", "mrcrev2b@aastu.edu.et", role="reviewer")
        r3 = make_user("mrcrev2c", "mrcrev2c@aastu.edu.et", role="reviewer")

        for r in (r1, r2, r3):
            DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=r)

        ModerationDecision.objects.create(dataset=dataset, reviewer=r1, decision="approved")

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/users/{r1.id}/revoke-role/", {"role": "reviewer"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.assertTrue(
            ModerationDecision.objects.filter(dataset=dataset, reviewer=r1).exists()
        )
        self.assertEqual(resp.data["released_datasets"], [])