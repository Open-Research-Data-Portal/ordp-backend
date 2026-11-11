from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import UserProfile
from apps.datasets.factories import make_user, make_college
from apps.datasets.models import Dataset, Contributor, PendingContentUpdate, PendingContentUpdateVote
from apps.datasets.services.revisions import resolve_content_update_votes, route_change
from apps.notifications.models import Notification
from apps.access_control.models import AccessRequest, UserPermissionGrant
from .models import SharePermission, DatasetAccessRequest


def make_dataset_with_version(owner, title="Test DS", visibility="restricted"):
    dataset = Dataset.objects.create(title=title, owner=owner, visibility=visibility, status=Dataset.Status.APPROVED)
    route_change(dataset=dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=owner,
                 new_file_key="f.csv", diff_percentage=100.0, change_summary={}, proposed_metadata={})
    update = PendingContentUpdate.objects.get(dataset=dataset)

    for i in range(3):  # MIN_REVIEWER_QUORUM
        checker = make_user(
            f"{title.replace(' ', '')}setupchecker{i}",
            f"{title.replace(' ', '')}setupchecker{i}@aastu.edu.et",
            role="reviewer",
        )
        PendingContentUpdateVote.objects.create(update=update, reviewer=checker, vote="approve")
    resolve_content_update_votes(update)

    dataset.refresh_from_db()
    return dataset




class FreeDownloadTests(APITestCase):
    def test_owner_downloads_own_restricted_dataset_freely(self):
        owner = make_user("fdowner", "fdowner@aastu.edu.et")
        dataset = make_dataset_with_version(owner)
        self.client.force_authenticate(owner)
        resp = self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_contributor_downloads_own_restricted_dataset_freely(self):
        owner = make_user(
            "fdowner2",
            "fdowner2@aastu.edu.et",
        )

        contributor = make_user(
            "contributor",
            "contributor@aastu.edu.et",
            role="public",
        )

        college = make_college("Contributor College")

        profile = contributor.profile
        profile.full_name = "Contributor"
        profile.affiliation = "AASTU"
        profile.academia = UserProfile.Academia.RESEARCHER
        profile.college = college
        profile.profile_visibility = "public"
        profile.terms_accepted = True
        profile.can_upload_datasets = True
        profile.save()

        dataset = make_dataset_with_version(owner)

        Contributor.objects.create(
            dataset=dataset,
            user=contributor,
            name="Contributor",
            contributor_type="contributor",
        )

        self.client.force_authenticate(contributor)

        resp = self.client.get(
            f"/api/sharing/{dataset.id}/download/"
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_public_role_contributor_cannot_download_freely(self):
        """Free download requires role=researcher, per user_can_freely_download."""
        owner = make_user("fdowner3", "fdowner3@aastu.edu.et")
        public_contributor = make_user("fdpubcontrib", "fdpubcontrib@aastu.edu.et", role="public")
        dataset = make_dataset_with_version(owner)
        Contributor.objects.create(dataset=dataset, user=public_contributor, name="P", contributor_type="contributor")

        self.client.force_authenticate(public_contributor)
        resp = self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_stranger_cannot_download_restricted_without_share_permission(self):
        owner = make_user("fdowner4", "fdowner4@aastu.edu.et")
        stranger = make_user("fdstranger", "fdstranger@aastu.edu.et")
        dataset = make_dataset_with_version(owner)

        self.client.force_authenticate(stranger)
        resp = self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_download_increments_counter(self):
        owner = make_user("fdowner5", "fdowner5@aastu.edu.et")
        dataset = make_dataset_with_version(owner)
        self.client.force_authenticate(owner)
        self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.client.get(f"/api/sharing/{dataset.id}/download/")
        dataset.refresh_from_db()
        self.assertEqual(dataset.download_count, 2)


class ViewCounterTests(APITestCase):
    def test_view_increments_counter_and_is_separate_from_download(self):
        owner = make_user("vcowner", "vcowner@aastu.edu.et")
        dataset = make_dataset_with_version(owner)
        self.client.force_authenticate(owner)
        self.client.get(f"/api/datasets/{dataset.id}/")
        self.client.get(f"/api/datasets/{dataset.id}/")
        self.client.get(f"/api/sharing/{dataset.id}/download/")
        dataset.refresh_from_db()
        self.assertEqual(dataset.view_count, 2)
        self.assertEqual(dataset.download_count, 1)


class PublicInstitutionalShareTests(APITestCase):
    def test_public_share_request_resolves_immediately_no_vote(self):
        owner = make_user("pubowner", "pubowner@aastu.edu.et")
        requester = make_user("pubreq", "pubreq@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "Public DS", visibility="public")

        self.client.force_authenticate(requester)
        resp = self.client.post(f"/api/sharing/{dataset.id}/request-share/", {"purpose": "research"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["share_ready"])
        self.assertEqual(resp.data["status"], "approved")
        self.assertIn("download_url", resp.data)
        self.assertFalse(DatasetAccessRequest.objects.filter(dataset=dataset).exists())

        dataset.refresh_from_db()
        self.assertEqual(dataset.download_count, 1)


class RestrictedShareVotingTests(APITestCase):
    """Legacy share vote/owner-decision endpoints are retired (410).
    Restricted requests now create V2 AccessRequest rows under /api/access-control/.
    """

    def setUp(self):
        self.owner = make_user("rsvowner", "rsvowner@aastu.edu.et")
        self.requester = make_user("rsvreq", "rsvreq@aastu.edu.et")
        self.dataset = make_dataset_with_version(self.owner, "Restricted DS")

    def test_without_justification_is_blocked(self):
        self.client.force_authenticate(self.requester)
        resp = self.client.post(f"/api/sharing/{self.dataset.id}/request-share/", {"purpose": "research"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_restricted_request_creates_v2_access_request(self):
        self.client.force_authenticate(self.requester)
        resp = self.client.post(
            f"/api/sharing/{self.dataset.id}/request-share/",
            {
                "purpose": "research",
                "justification": "Needed for my thesis.",
                "purpose_type": "read",
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "pending")
        self.assertTrue(AccessRequest.objects.filter(id=resp.data["request_id"], requester=self.requester).exists())
        self.assertFalse(
            SharePermission.objects.filter(dataset=self.dataset, shared_with_user=self.requester).exists()
        )

    def test_legacy_vote_endpoint_returns_410(self):
        self.client.force_authenticate(self.requester)
        resp = self.client.post(
            "/api/sharing/access-requests/00000000-0000-0000-0000-000000000001/vote/",
            {"vote": "approve"},
        )
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)
        self.assertIn("access-control", resp.data["detail"])

    def test_legacy_owner_decision_endpoint_returns_410(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/sharing/access-requests/00000000-0000-0000-0000-000000000001/owner-decision/",
            {"decision": "approve"},
        )
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)
        self.assertIn("access-control", resp.data["detail"])

    def test_legacy_queue_endpoint_returns_410(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get("/api/sharing/access-requests/queue/")
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)


class CoauthorInvitationTests(APITestCase):
    def test_invitation_defaults_to_view_and_returns_existing_user_match(self):
        owner = make_user("defowner", "defowner@aastu.edu.et")
        invitee = make_user("definvitee", "definvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "Default Permission DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "definvitee@aastu.edu.et"})

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["permission"], "view")
        self.assertEqual(resp.data["invited_email"], "definvitee@aastu.edu.et")
        self.assertEqual(resp.data["matched_user"]["id"], invitee.id)
        self.assertEqual(resp.data["matched_user"]["email"], invitee.email)

        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(id=resp.data["invitation_id"])
        self.assertEqual(invitation.permission, "view")

    def test_manual_email_invitation_without_existing_user_is_allowed(self):
        owner = make_user("manualowner", "manualowner@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "Manual Email DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(
            f"/api/sharing/{dataset.id}/invite-coauthor/",
            {"email": "future.coauthor@aastu.edu.et", "permission": "edit"},
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data["permission"], "edit")
        self.assertEqual(resp.data["invited_email"], "future.coauthor@aastu.edu.et")
        self.assertIsNone(resp.data["matched_user"])

    def test_invited_existing_user_must_accept_before_getting_access(self):
        owner = make_user("casowner", "casowner@aastu.edu.et")
        invitee = make_user("casinvitee", "casinvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "CAS DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "casinvitee@aastu.edu.et", "permission": "view"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        self.assertFalse(
            UserPermissionGrant.objects.filter(
                user=invitee, permission_id="dataset.view", resource_type="dataset", resource_id=dataset.id,
                revoked_at__isnull=True,
            ).exists()
        )

        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(dataset=dataset, invited_email="casinvitee@aastu.edu.et")
        self.assertEqual(invitation.permission, "view")

        self.client.force_authenticate(invitee)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_resp.data["contributor_type"], "co_author")

        self.assertTrue(
            UserPermissionGrant.objects.filter(
                user=invitee, permission_id="dataset.view", resource_type="dataset", resource_id=dataset.id,
                revoked_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            UserPermissionGrant.objects.filter(
                user=invitee, permission_id="dataset.download", resource_type="dataset", resource_id=dataset.id,
                revoked_at__isnull=True,
            ).exists()
        )

        from apps.datasets.models import Contributor
        contributor = Contributor.objects.get(dataset=dataset, user=invitee)
        self.assertEqual(contributor.permission, "view")

    def test_wrong_email_cannot_accept_someone_elses_invitation(self):
        owner = make_user("wecowner", "wecowner@aastu.edu.et")
        wrong_user = make_user("wecwrong", "wecwrong@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "WEC DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "someoneelse@aastu.edu.et"})
        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(id=resp.data["invitation_id"])

        self.client.force_authenticate(wrong_user)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_invitation_cannot_be_accepted(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.datasets.models import DatasetInvitation

        owner = make_user("expowner", "expowner@aastu.edu.et")
        invitee = make_user("expinvitee", "expinvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "EXP DS")
        invitation = DatasetInvitation.objects.create(
            dataset=dataset, invited_email="expinvitee@aastu.edu.et", role="co_author",
            invited_by=owner, expires_at=timezone.now() - timedelta(days=1),
        )

        self.client.force_authenticate(invitee)
        resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoked_invitation_cannot_be_accepted(self):
        owner = make_user("revowner", "revowner@aastu.edu.et")
        invitee = make_user("revinvitee", "revinvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "REV DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "revinvitee@aastu.edu.et"})
        invitation_id = resp.data["invitation_id"]

        revoke_resp = self.client.post(f"/api/sharing/{dataset.id}/invitations/{invitation_id}/revoke/")
        self.assertEqual(revoke_resp.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(invitee)
        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(id=invitation_id)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_coauthor_invitation_assigns_correct_type(self):
        owner = make_user("coaowner", "coaowner@aastu.edu.et")
        invitee = make_user("coainvitee", "coainvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "COA DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "coainvitee@aastu.edu.et"})
        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(id=resp.data["invitation_id"])

        self.client.force_authenticate(invitee)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.data["contributor_type"], "co_author")

    def test_invitation_not_delivered_until_dataset_published(self):
        from django.core import mail
        from apps.datasets.models import Dataset, DatasetInvitation
        owner = make_user("pendowner", "pendowner@aastu.edu.et")
        invitee = make_user("pendinvitee", "pendinvitee@aastu.edu.et")
        dataset = Dataset.objects.create(title="Pending DS", owner=owner, status=Dataset.Status.PENDING)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-coauthor/", {"email": "pendinvitee@aastu.edu.et"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        invitation = DatasetInvitation.objects.get(id=resp.data["invitation_id"])
        self.assertIsNone(invitation.notified_at)
        self.assertEqual(len(mail.outbox), 0)

        self.client.force_authenticate(invitee)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_400_BAD_REQUEST)

        from apps.datasets.services.invitations import dispatch_pending_invitations
        dataset.status = Dataset.Status.PUBLISHED
        dataset.save(update_fields=["status"])
        dispatch_pending_invitations(dataset)

        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.notified_at)
        self.assertEqual(len(mail.outbox), 1)

        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_200_OK)
