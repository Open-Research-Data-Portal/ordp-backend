from django.conf import settings
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import (
    Dataset, DatasetFile, Contributor, RevisionRequest, PendingContentUpdate,
)
from apps.datasets.services.revisions import route_change, resolve_revision_request_votes, resolve_content_update_votes
from apps.metadata.models import Category, Subject, Metadata


def make_published_dataset(owner, title="Editing DS"):
    dataset = Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.PUBLISHED)
    DatasetFile.objects.create(dataset=dataset, file_key="k1", file_type="csv", file_size=100, checksum="a")
    category = Category.objects.create(name=f"{title} Cat", status=Category.Status.APPROVED)
    subject = Subject.objects.create(name=f"{title} Subj")
    Metadata.objects.create(dataset=dataset, description="test", category=category, subject=subject)
    return dataset


class RequestRevisionPermissionTests(APITestCase):
    def test_outsider_can_request_permission(self):
        owner = make_user("rrpowner", "rrpowner@aastu.edu.et")
        outsider = make_user("rrpoutsider", "rrpoutsider@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner)

        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/{dataset.id}/request-revision-permission/", {"reason": "Found an error."})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(RevisionRequest.objects.filter(dataset=dataset, requester=outsider).exists())

    def test_owner_cannot_request_permission(self):
        owner = make_user("rrpowner2", "rrpowner2@aastu.edu.et")
        dataset = make_published_dataset(owner)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/request-revision-permission/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_existing_contributor_cannot_request_permission(self):
        owner = make_user("rrpowner3", "rrpowner3@aastu.edu.et")
        contributor_user = make_user("rrpcontrib", "rrpcontrib@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner)
        Contributor.objects.create(dataset=dataset, user=contributor_user, name="C", contributor_type="contributor")

        self.client.force_authenticate(contributor_user)
        resp = self.client.post(f"/api/datasets/{dataset.id}/request-revision-permission/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reason_required(self):
        owner = make_user("rrpowner4", "rrpowner4@aastu.edu.et")
        outsider = make_user("rrpoutsider2", "rrpoutsider2@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner)

        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/{dataset.id}/request-revision-permission/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class RevisionRequestVotingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("rrvowner", "rrvowner@aastu.edu.et")
        self.requester = make_user("rrvrequester", "rrvrequester@aastu.edu.et", role="researcher")
        self.dataset = make_published_dataset(self.owner, "RRV DS")
        self.reviewers = [make_user(f"rrvreviewer{i}", f"rrvreviewer{i}@aastu.edu.et", role="checker") for i in range(3)]

        self.client.force_authenticate(self.requester)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/request-revision-permission/", {"reason": "test"})
        self.request_id = resp.data["request_id"]

    def test_majority_approve_grants_permission(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            resp = self.client.post(f"/api/admin-panel/revision-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "approved")

        request_obj = RevisionRequest.objects.get(id=self.request_id)
        self.assertTrue(request_obj.status == "approved" and not request_obj.used)

    def test_majority_reject_blocks_permission(self):
        for i, reviewer in enumerate(self.reviewers):
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/revision-requests/{self.request_id}/vote/",
                              {"vote": "approve" if i == 0 else "reject"})
        request_obj = RevisionRequest.objects.get(id=self.request_id)
        self.assertEqual(request_obj.status, "rejected")

    def test_resolved_request_rejects_further_votes(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/revision-requests/{self.request_id}/vote/", {"vote": "approve"})
        late_reviewer = make_user("rrvlate", "rrvlate@aastu.edu.et", role="checker")
        self.client.force_authenticate(late_reviewer)
        resp = self.client.post(f"/api/admin-panel/revision-requests/{self.request_id}/vote/", {"vote": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_researcher_cannot_vote(self):
        researcher = make_user("rrvresearcher", "rrvresearcher@aastu.edu.et", role="researcher")
        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/admin-panel/revision-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class ProposeRevisionPermissionTests(APITestCase):
    def test_propose_without_approved_request_is_blocked(self):
        owner = make_user("prpowner", "prpowner@aastu.edu.et")
        outsider = make_user("prpoutsider", "prpoutsider@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner)

        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/{dataset.id}/propose-revision/", {"submitter_message": "fix"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_propose_with_incomplete_profile_is_blocked(self):
        owner = make_user("prpowner2", "prpowner2@aastu.edu.et")
        outsider = make_user("prpoutsider2", "prpoutsider2@aastu.edu.et", role="researcher")
        outsider.profile.academia = ""
        outsider.profile.department = None
        outsider.profile.terms_accepted = False
        outsider.profile.save()

        dataset = make_published_dataset(owner)
        RevisionRequest.objects.create(dataset=dataset, requester=outsider, reason="test", status="approved")

        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/{dataset.id}/propose-revision/", {"submitter_message": "fix"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("profile", resp.data["detail"].lower())

    def test_propose_without_message_is_blocked(self):
        from apps.accounts.models import UserProfile, College, Department
        owner = make_user("prpowner3", "prpowner3@aastu.edu.et")
        outsider = make_user("prpoutsider3", "prpoutsider3@aastu.edu.et", role="researcher")
        college = College.objects.create(name="PRP College")
        department = Department.objects.create(name="PRP Dept", college=college)
        outsider.profile.academia = "researcher"
        outsider.profile.department = department
        outsider.profile.terms_accepted = True
        outsider.profile.save()
        dataset = make_published_dataset(owner)
        RevisionRequest.objects.create(dataset=dataset, requester=outsider, reason="test", status="approved")

        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/{dataset.id}/propose-revision/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class RouteChangeThresholdTests(APITestCase):
    """Unit-level tests on route_change directly — doesn't need the full
    chunked-upload pipeline, since route_change just takes a diff_percentage."""

    def test_diff_below_threshold_applies_immediately(self):
        owner = make_user("rctowner", "rctowner@aastu.edu.et")
        dataset = make_published_dataset(owner, "RCT Minor DS")

        with override_settings(VERSION_BUMP_THRESHOLD_PCT=50.0):
            result = route_change(
                dataset=dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=owner,
                new_file_key="new-key", diff_percentage=5.0, change_summary={}, proposed_metadata={},
            )
        self.assertEqual(result["status"], "applied")
        dataset.refresh_from_db()
        self.assertEqual(DatasetFile.objects.get(dataset=dataset).file_key, "new-key")

    def test_diff_at_or_above_threshold_goes_to_committee(self):
        owner = make_user("rctowner2", "rctowner2@aastu.edu.et")
        dataset = make_published_dataset(owner, "RCT Major DS")

        with override_settings(VERSION_BUMP_THRESHOLD_PCT=50.0):
            result = route_change(
                dataset=dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=owner,
                new_file_key="new-key", diff_percentage=90.0, change_summary={}, proposed_metadata={},
            )
        self.assertEqual(result["status"], "pending_review")
        self.assertTrue(PendingContentUpdate.objects.filter(id=result["pending_update_id"]).exists())


class ContentUpdateVotingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("cuvowner", "cuvowner@aastu.edu.et")
        self.dataset = make_published_dataset(self.owner, "CUV DS")
        self.reviewers = [make_user(f"cuvreviewer{i}", f"cuvreviewer{i}@aastu.edu.et", role="checker") for i in range(3)]
        self.update = PendingContentUpdate.objects.create(
            dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
            new_file_key="new-key", diff_percentage=90.0, change_summary={}, proposed_metadata={},
        )

    def test_majority_approve_applies_update(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            resp = self.client.post(f"/api/admin-panel/content-updates/{self.update.id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "approved")
        self.update.refresh_from_db()
        self.assertEqual(self.update.status, "approved")
        self.assertEqual(DatasetFile.objects.get(dataset=self.dataset).file_key, "new-key")

    def test_majority_reject_does_not_apply(self):
        for i, reviewer in enumerate(self.reviewers):
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/content-updates/{self.update.id}/vote/",
                              {"vote": "approve" if i == 0 else "reject"})
        self.update.refresh_from_db()
        self.assertEqual(self.update.status, "rejected")
        self.assertNotEqual(DatasetFile.objects.get(dataset=self.dataset).file_key, "new-key")


class WatcherNotificationTests(APITestCase):
    def test_toggle_watch_adds_and_removes(self):
        owner = make_user("wnowner", "wnowner@aastu.edu.et")
        watcher = make_user("wnwatcher", "wnwatcher@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner)

        self.client.force_authenticate(watcher)
        resp = self.client.post(f"/api/datasets/{dataset.id}/watch/")
        self.assertTrue(resp.data["watching"])

        resp = self.client.post(f"/api/datasets/{dataset.id}/watch/")
        self.assertFalse(resp.data["watching"])

    def test_watcher_notified_on_minor_change(self):
        from apps.notifications.models import Notification
        from apps.datasets.models import DatasetWatcher

        owner = make_user("wmcowner", "wmcowner@aastu.edu.et")
        watcher = make_user("wmcwatcher", "wmcwatcher@aastu.edu.et", role="researcher")
        dataset = make_published_dataset(owner, "WMC DS")
        DatasetWatcher.objects.create(dataset=dataset, user=watcher)

        with override_settings(VERSION_BUMP_THRESHOLD_PCT=50.0):
            route_change(
                dataset=dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=owner,
                new_file_key="minor-key", diff_percentage=5.0, change_summary={}, proposed_metadata={},
            )
        self.assertTrue(Notification.objects.filter(user=watcher, dataset=dataset).exists())
        self.assertFalse(DatasetWatcher.objects.filter(dataset=dataset, user=watcher).exists())  # one-shot, cleared