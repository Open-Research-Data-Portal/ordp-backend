from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.admin_panel.models import (
    DatasetArchiveRequest, ArchiveRequestVote,
    DatasetUnarchiveRequest, 
)
from apps.notifications.models import Notification


def make_dataset(owner, title="Test DS", status=Dataset.Status.PUBLISHED):
    return Dataset.objects.create(title=title, owner=owner, status=status)


def make_archived_dataset(owner, title="Archived DS"):
    dataset = make_dataset(owner, title)
    dataset.is_archived = True
    dataset.save(update_fields=["is_archived"])
    return dataset


# ---------------------------------------------------------------------
# Archive request creation
# ---------------------------------------------------------------------

class RequestArchiveTests(APITestCase):
    def test_owner_can_request_archive(self):
        owner = make_user("arowner", "arowner@aastu.edu.et")
        dataset = make_dataset(owner)

        self.client.force_authenticate(owner)
        resp = self.client.post(
            f"/api/datasets/{dataset.id}/archive/",
            {"reason_category": "outdated", "reason": "No longer needed."},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(DatasetArchiveRequest.objects.filter(dataset=dataset, requested_by=owner).exists())
    def test_non_owner_cannot_request_archive(self):
        owner = make_user("arowner2", "arowner2@aastu.edu.et")
        stranger = make_user("arstranger", "arstranger@aastu.edu.et")
        dataset = make_dataset(owner)

        self.client.force_authenticate(stranger)
        resp = self.client.post(
            f"/api/datasets/{dataset.id}/archive/",
            {"reason_category": "outdated", "reason": "test"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_coowner_cannot_request_archive(self):
        """Deliberately stricter than IsDatasetOwner/is_owned_by — archive is
        owner-field only for now, co-owners excluded."""
        from apps.datasets.models import Contributor

        owner = make_user("arowner3", "arowner3@aastu.edu.et")
        coowner_user = make_user("arcoowner", "arcoowner@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner)
        Contributor.objects.create(
            dataset=dataset, user=coowner_user, name="CoOwner",
            contributor_type=Contributor.ContributorType.OWNER,
        )

        self.client.force_authenticate(coowner_user)
        resp = self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_reason_required(self):
        owner = make_user("arowner4", "arowner4@aastu.edu.et")
        dataset = make_dataset(owner)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/archive/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_published_dataset_cannot_be_archived(self):
        owner = make_user("arowner5", "arowner5@aastu.edu.et")
        dataset = make_dataset(owner, status=Dataset.Status.DRAFT)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_already_archived_dataset_rejected(self):
        owner = make_user("arowner6", "arowner6@aastu.edu.et")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_pending_request_rejected(self):
        owner = make_user("arowner7", "arowner7@aastu.edu.et")
        dataset = make_dataset(owner)

        self.client.force_authenticate(owner)
        self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason_category": "outdated", "reason": "first"})
        resp = self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason_category": "outdated", "reason": "second"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DatasetArchiveRequest.objects.filter(dataset=dataset).count(), 1)

    def test_committee_notified_on_request(self):
        owner = make_user("arowner8", "arowner8@aastu.edu.et")
        reviewer = make_user("arreviewer8", "arreviewer8@aastu.edu.et", role="reviewer")
        dataset = make_dataset(owner)

        self.client.force_authenticate(owner)
        self.client.post(f"/api/datasets/{dataset.id}/archive/", {"reason_category": "outdated", "reason": "test"})
        self.assertTrue(Notification.objects.filter(
            user=reviewer, notification_type=Notification.NotificationType.ARCHIVE_REQUESTED,
        ).exists())


# ---------------------------------------------------------------------
# Archive voting
# ---------------------------------------------------------------------

class ArchiveVotingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("avowner", "avowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner, "AV DS")
        self.reviewers = [
            make_user(f"avreviewer{i}", f"avreviewer{i}@aastu.edu.et", role="reviewer") for i in range(3)
        ]
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/archive/",
            {"reason_category": "outdated", "reason": "cleanup"},
        )
        self.request_id = resp.data["request_id"]
    def test_stays_pending_below_quorum(self):
        self.client.force_authenticate(self.reviewers[0])
        resp = self.client.post(f"/api/admin-panel/archive-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "pending")
        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.is_archived)

    def test_majority_approve_archives_dataset_and_notifies_owner(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            resp = self.client.post(f"/api/admin-panel/archive-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "approved")

        self.dataset.refresh_from_db()
        self.assertTrue(self.dataset.is_archived)
        self.assertIsNotNone(self.dataset.archived_at)
        self.assertTrue(Notification.objects.filter(
            user=self.owner, notification_type=Notification.NotificationType.DATASET_ARCHIVED,
        ).exists())

    def test_majority_reject_leaves_dataset_untouched_and_notifies_owner(self):
        for i, reviewer in enumerate(self.reviewers):
            self.client.force_authenticate(reviewer)
            self.client.post(
                f"/api/admin-panel/archive-requests/{self.request_id}/vote/",
                {"vote": "approve" if i == 0 else "reject"},
            )
        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.is_archived)
        self.assertTrue(Notification.objects.filter(
            user=self.owner, notification_type=Notification.NotificationType.ARCHIVE_REJECTED,
        ).exists())

    def test_resolved_request_rejects_further_votes(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/archive-requests/{self.request_id}/vote/", {"vote": "approve"})
        late_reviewer = make_user("avlate", "avlate@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(late_reviewer)
        resp = self.client.post(f"/api/admin-panel/archive-requests/{self.request_id}/vote/", {"vote": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_researcher_cannot_vote(self):
        researcher = make_user("avresearcher", "avresearcher@aastu.edu.et", role="researcher")
        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/admin-panel/archive-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------
# Unarchive request creation — any authenticated user, not owner-only
# ---------------------------------------------------------------------

class RequestUnarchiveTests(APITestCase):
    def test_any_authenticated_user_can_request_unarchive(self):
        owner = make_user("uaowner", "uaowner@aastu.edu.et")
        stranger = make_user("uastranger", "uastranger@aastu.edu.et", role="public")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(stranger)
        resp = self.client.post(
            f"/api/datasets/{dataset.id}/unarchive/",
            {"intended_use": "research", "reason": "I need this data."},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(DatasetUnarchiveRequest.objects.filter(dataset=dataset, requested_by=stranger).exists())
    def test_non_archived_dataset_returns_404(self):
        owner = make_user("uaowner2", "uaowner2@aastu.edu.et")
        stranger = make_user("uastranger2", "uastranger2@aastu.edu.et")
        dataset = make_dataset(owner)  # not archived

        self.client.force_authenticate(stranger)
        resp = self.client.post(f"/api/datasets/{dataset.id}/unarchive/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_reason_required(self):
        owner = make_user("uaowner3", "uaowner3@aastu.edu.et")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/unarchive/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_pending_request_rejected(self):
        owner = make_user("uaowner4", "uaowner4@aastu.edu.et")
        requester1 = make_user("uareq1", "uareq1@aastu.edu.et")
        requester2 = make_user("uareq2", "uareq2@aastu.edu.et")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(requester1)
        self.client.post(f"/api/datasets/{dataset.id}/unarchive/", {"intended_use": "research", "reason": "first"})

        self.client.force_authenticate(requester2)
        resp = self.client.post(f"/api/datasets/{dataset.id}/unarchive/", {"intended_use": "research", "reason": "second"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(DatasetUnarchiveRequest.objects.filter(dataset=dataset).count(), 1)
# ---------------------------------------------------------------------
# Unarchive voting
# ---------------------------------------------------------------------

class UnarchiveDecisionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("uvowner", "uvowner@aastu.edu.et")
        self.requester = make_user("uvrequester", "uvrequester@aastu.edu.et")
        self.dataset = make_archived_dataset(self.owner, "UV DS")
        self.admin = make_user("uvadmin", "uvadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(self.requester)
        resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/unarchive/",
            {"intended_use": "research", "reason": "need it back"},
        )
        self.request_id = resp.data["request_id"]

    def test_admin_approve_restores_dataset_and_notifies_owner(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(f"/api/admin-panel/unarchive-requests/{self.request_id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.data["status"], "approved")

        self.dataset.refresh_from_db()
        self.assertFalse(self.dataset.is_archived)
        self.assertTrue(Notification.objects.filter(
            user=self.owner, notification_type=Notification.NotificationType.DATASET_RESTORED,
        ).exists())

    def test_admin_reject_notifies_requester(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/api/admin-panel/unarchive-requests/{self.request_id}/decide/", {"decision": "reject"})

        self.dataset.refresh_from_db()
        self.assertTrue(self.dataset.is_archived)
        self.assertTrue(Notification.objects.filter(
            user=self.requester, notification_type=Notification.NotificationType.UNARCHIVE_REJECTED,
        ).exists())

    def test_reviewer_cannot_decide(self):
        reviewer = make_user("uvreviewer", "uvreviewer@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(reviewer)
        resp = self.client.post(f"/api/admin-panel/unarchive-requests/{self.request_id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_resolved_request_rejects_further_decisions(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/api/admin-panel/unarchive-requests/{self.request_id}/decide/", {"decision": "approve"})
        resp = self.client.post(f"/api/admin-panel/unarchive-requests/{self.request_id}/decide/", {"decision": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
# ---------------------------------------------------------------------
# Admin bypass restore
# ---------------------------------------------------------------------

class AdminRestoreTests(APITestCase):
    def test_admin_can_restore_without_reason_or_vote(self):
        owner = make_user("arestowner", "arestowner@aastu.edu.et")
        admin = make_user("arestadmin", "arestadmin@aastu.edu.et", role="admin")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        dataset.refresh_from_db()
        self.assertFalse(dataset.is_archived)
        self.assertTrue(Notification.objects.filter(
            user=owner, notification_type=Notification.NotificationType.DATASET_RESTORED,
        ).exists())

    def test_non_admin_cannot_bypass_restore(self):
        owner = make_user("arestowner2", "arestowner2@aastu.edu.et")
        reviewer = make_user("arestreviewer", "arestreviewer@aastu.edu.et", role="reviewer")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(reviewer)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_restore_logs_activity(self):
        from apps.accounts.models import ActivityLog

        owner = make_user("arestowner3", "arestowner3@aastu.edu.et")
        admin = make_user("arestadmin3", "arestadmin3@aastu.edu.et", role="admin")
        dataset = make_archived_dataset(owner)

        self.client.force_authenticate(admin)
        self.client.post(f"/api/admin-panel/datasets/{dataset.id}/restore/")
        self.assertTrue(ActivityLog.objects.filter(
            action="dataset_admin_restored", target_object=f"Dataset:{dataset.id}",
        ).exists())


# ---------------------------------------------------------------------
# Visibility / listing behavior
# ---------------------------------------------------------------------

class ArchivedVisibilityTests(APITestCase):
    def test_archived_dataset_excluded_from_search(self):
        owner = make_user("visowner", "visowner@aastu.edu.et")
        dataset = make_archived_dataset(owner, "Hidden From Search DS")

        resp = self.client.get("/api/search/datasets/")
        titles = {d["title"] for d in resp.data}
        self.assertNotIn("Hidden From Search DS", titles)

    def test_archived_dataset_excluded_from_researcher_feed(self):
        from apps.metadata.models import Category, Metadata

        researcher = make_user("visresearcher", "visresearcher@aastu.edu.et", role="researcher")
        other_owner = make_user("visowner2", "visowner2@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Vis Cat")
        researcher.profile.interests.set([category])

        dataset = make_archived_dataset(other_owner, "Archived Feed DS")
        Metadata.objects.create(dataset=dataset, description="test", category=category)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/datasets/dashboard/feed/")
        titles = {d["title"] for d in resp.data}
        self.assertNotIn("Archived Feed DS", titles)

    def test_archived_dataset_still_visible_in_my_datasets(self):
        owner = make_user("visowner3", "visowner3@aastu.edu.et", role="researcher")
        dataset = make_archived_dataset(owner, "Still Mine DS")

        self.client.force_authenticate(owner)
        resp = self.client.get("/api/datasets/mine/")
        titles = {d["title"] for d in resp.data}
        self.assertIn("Still Mine DS", titles)

    def test_archived_datasets_listing_shows_it_to_any_user(self):
        owner = make_user("visowner4", "visowner4@aastu.edu.et")
        any_user = make_user("visany", "visany@aastu.edu.et", role="public")
        dataset = make_archived_dataset(owner, "In Archive Listing DS")

        self.client.force_authenticate(any_user)
        resp = self.client.get("/api/datasets/archived/")
        titles = {d["title"] for d in resp.data}
        self.assertIn("In Archive Listing DS", titles)
    def test_archived_dataset_detail_hidden_from_strangers(self):
        owner = make_user("dvisowner", "dvisowner@aastu.edu.et")
        stranger = make_user("dvisstranger", "dvisstranger@aastu.edu.et")
        dataset = make_archived_dataset(owner, "Hidden Detail DS")

        self.client.force_authenticate(stranger)
        resp = self.client.get(f"/api/datasets/{dataset.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_archived_dataset_detail_visible_to_owner(self):
        owner = make_user("dvisowner2", "dvisowner2@aastu.edu.et")
        dataset = make_archived_dataset(owner, "Owner Visible DS")

        self.client.force_authenticate(owner)
        resp = self.client.get(f"/api/datasets/{dataset.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_archived_dataset_detail_visible_to_reviewer(self):
        owner = make_user("dvisowner3", "dvisowner3@aastu.edu.et")
        reviewer = make_user("dvisreviewer", "dvisreviewer@aastu.edu.et", role="reviewer")
        dataset = make_archived_dataset(owner, "Reviewer Visible DS")

        self.client.force_authenticate(reviewer)
        resp = self.client.get(f"/api/datasets/{dataset.id}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_archived_dataset_detail_hidden_from_anonymous(self):
        owner = make_user("dvisowner4", "dvisowner4@aastu.edu.et")
        dataset = make_archived_dataset(owner, "Anon Hidden DS")

        resp = self.client.get(f"/api/datasets/{dataset.id}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)