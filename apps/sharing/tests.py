from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, Contributor, PendingContentUpdate, PendingContentUpdateVote
from apps.datasets.services.revisions import resolve_content_update_votes, route_change
from apps.notifications.models import Notification
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

    def test_researcher_contributor_downloads_own_restricted_dataset_freely(self):
        owner = make_user("fdowner2", "fdowner2@aastu.edu.et")
        contributor = make_user("fdcontrib", "fdcontrib@aastu.edu.et")
        dataset = make_dataset_with_version(owner)
        Contributor.objects.create(dataset=dataset, user=contributor, name="C", contributor_type="contributor")

        self.client.force_authenticate(contributor)
        resp = self.client.get(f"/api/sharing/{dataset.id}/download/")
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
        self.assertFalse(DatasetAccessRequest.objects.filter(dataset=dataset).exists())


class RestrictedShareVotingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("rsvowner", "rsvowner@aastu.edu.et")
        self.requester = make_user("rsvreq", "rsvreq@aastu.edu.et")
        self.dataset = make_dataset_with_version(self.owner, "Restricted DS")
        self.reviewers = [
            make_user(f"rsvreviewer{i}", f"rsvreviewer{i}@aastu.edu.et", role="reviewer") for i in range(3)
        ]

    def _submit_request(self, purpose_type="read"):
        self.client.force_authenticate(self.requester)
        resp = self.client.post(f"/api/sharing/{self.dataset.id}/request-share/",
                                 {"purpose": "research", "justification": "Needed for my thesis.",
                                  "purpose_type": purpose_type})
        return resp.data["request_id"]

    def test_without_justification_is_blocked(self):
        self.client.force_authenticate(self.requester)
        resp = self.client.post(f"/api/sharing/{self.dataset.id}/request-share/", {"purpose": "research"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stays_pending_below_quorum(self):
        request_id = self._submit_request()
        self.client.force_authenticate(self.reviewers[0])
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "pending")
    def test_majority_approve_grants_access(self):
        request_id = self._submit_request()
        for reviewer in self.reviewers[:2]:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.client.force_authenticate(self.reviewers[2])
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "reject"})
        self.assertEqual(resp.data["status"], "pending")

        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/owner-decision/", {"decision": "approve"})
        self.assertEqual(resp.data["status"], "approved")  #

        self.assertTrue(SharePermission.objects.filter(dataset=self.dataset, shared_with_user=self.requester).exists())
        self.client.force_authenticate(self.requester)
        dl_resp = self.client.get(f"/api/sharing/{self.dataset.id}/download/")
        self.assertEqual(dl_resp.status_code, status.HTTP_200_OK)

    def test_majority_reject_blocks_access(self):
        request_id = self._submit_request()
        for i, reviewer in enumerate(self.reviewers):
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/",
                              {"vote": "approve" if i == 0 else "reject"})
        self.assertFalse(SharePermission.objects.filter(dataset=self.dataset, shared_with_user=self.requester).exists())

    def test_edit_purpose_approved_sets_notice(self):
        request_id = self._submit_request(purpose_type="edit")
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/sharing/access-requests/{request_id}/owner-decision/", {"decision": "approve"})
        self.dataset.refresh_from_db()
        self.assertTrue(self.dataset.edit_in_progress_notice)

    def test_read_purpose_approved_does_not_set_notice(self):
        request_id = self._submit_request(purpose_type="read")
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.edit_in_progress_notice)

    def test_reviewer_can_change_their_vote(self):
        request_id = self._submit_request()
        self.client.force_authenticate(self.reviewers[0])
        self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "reject"})
        self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        access_request = DatasetAccessRequest.objects.get(id=request_id)
        self.assertEqual(access_request.votes.count(), 1)  # updated, not duplicated
        self.assertEqual(access_request.votes.first().vote, "approve")

    def test_non_reviewer_cannot_vote(self):
        request_id = self._submit_request()
        outsider = make_user("rsvoutsider", "rsvoutsider@aastu.edu.et")
        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_resolved_request_rejects_further_votes(self):
        request_id = self._submit_request()
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})
        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/sharing/access-requests/{request_id}/owner-decision/", {"decision": "approve"})

        late_reviewer = make_user("rsvlatereviewer", "rsvlatereviewer@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(late_reviewer)
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


    def test_owner_rejection_blocks_access_even_with_committee_majority(self):
        request_id = self._submit_request()
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/sharing/access-requests/{request_id}/vote/", {"vote": "approve"})

        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/owner-decision/", {"decision": "reject"})
        self.assertEqual(resp.data["status"], "rejected")
        self.assertFalse(SharePermission.objects.filter(dataset=self.dataset, shared_with_user=self.requester).exists())

    def test_non_owner_cannot_record_owner_decision(self):
        request_id = self._submit_request()
        self.client.force_authenticate(self.requester)  # not the owner
        resp = self.client.post(f"/api/sharing/access-requests/{request_id}/owner-decision/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class ContributorInvitationTests(APITestCase):
    def test_invited_existing_user_must_accept_before_getting_access(self):
        owner = make_user("casowner", "casowner@aastu.edu.et")
        invitee = make_user("casinvitee", "casinvitee@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "CAS DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-contributor/", {"email": "casinvitee@aastu.edu.et"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        # Not granted yet — invitation is pending, not accepted
        self.assertFalse(SharePermission.objects.filter(dataset=dataset, shared_with_user=invitee).exists())

        from apps.datasets.models import DatasetInvitation
        invitation = DatasetInvitation.objects.get(dataset=dataset, invited_email="casinvitee@aastu.edu.et")

        self.client.force_authenticate(invitee)
        accept_resp = self.client.post(f"/api/sharing/invitations/{invitation.token}/")
        self.assertEqual(accept_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(accept_resp.data["contributor_type"], "contributor")

        self.assertTrue(SharePermission.objects.filter(dataset=dataset, shared_with_user=invitee).exists())

    def test_wrong_email_cannot_accept_someone_elses_invitation(self):
        owner = make_user("wecowner", "wecowner@aastu.edu.et")
        wrong_user = make_user("wecwrong", "wecwrong@aastu.edu.et")
        dataset = make_dataset_with_version(owner, "WEC DS")

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-contributor/", {"email": "someoneelse@aastu.edu.et"})
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
            dataset=dataset, invited_email="expinvitee@aastu.edu.et", role="contributor",
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
        resp = self.client.post(f"/api/sharing/{dataset.id}/invite-contributor/", {"email": "revinvitee@aastu.edu.et"})
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