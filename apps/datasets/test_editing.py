from unittest.mock import patch
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import UserProfile
from apps.notifications.models import Notification
from apps.datasets.factories import make_user
from apps.datasets.models import (
    Dataset, DatasetFile, DatasetRevision, PendingContentUpdate, Contributor

)
from apps.datasets.services.revisions import route_change, apply_revision, decide_pending_content_update
from apps.accounts.models import ActivityLog

# ---------------------------------------------------------------------------
# Service-layer — no mocking, exercises the real DB logic
# ---------------------------------------------------------------------------

class RouteChangeAndReviewCycleTests(APITestCase):
    def setUp(self):
        self.owner = make_user("cyowner", "cyowner@aastu.edu.et")
        self.checker = make_user("cychecker", "cychecker@aastu.edu.et", role="checker")
        self.dataset = Dataset.objects.create(title="Cycle DS", owner=self.owner, status=Dataset.Status.APPROVED)
        DatasetFile.objects.create(dataset=self.dataset, file_key="v1.csv", file_type="csv", file_size=10, checksum="a")

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_below_threshold_applies_with_no_pending_update(self):
        result = route_change(
            dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
            new_file_key="v2.csv", diff_percentage=5.0, change_summary={}, proposed_metadata={},
        )
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "v2.csv")
        self.assertEqual(PendingContentUpdate.objects.filter(dataset=self.dataset).count(), 0)

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_above_threshold_holds_and_notifies_reviewers(self):
        result = route_change(
            dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
            new_file_key="v2.csv", diff_percentage=50.0, change_summary={}, proposed_metadata={},
        )
        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "v1.csv")
        self.assertTrue(Notification.objects.filter(
            user=self.checker, notification_type=Notification.NotificationType.CONTENT_UPDATE_PENDING
        ).exists())

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_reviewer_approval_applies_file_and_bumps_version(self):
        route_change(dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
                     new_file_key="v2.csv", diff_percentage=50.0, change_summary={}, proposed_metadata={})
        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        decide_pending_content_update(update, "approve", self.checker)

        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "v2.csv")
        self.assertEqual(self.dataset.version, 2)
        update.refresh_from_db()
        self.assertEqual(update.status, "approved")
        self.assertEqual(update.reviewed_by, self.checker)

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_reviewer_rejection_leaves_live_file_and_version_untouched(self):
        route_change(dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
                     new_file_key="v2.csv", diff_percentage=50.0, change_summary={}, proposed_metadata={})
        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        decide_pending_content_update(update, "reject", self.checker, reason="Unrelated data was swapped in.")

        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "v1.csv")
        self.assertEqual(self.dataset.version, 1)
        notif = Notification.objects.get(user=self.owner, notification_type=Notification.NotificationType.REVISION_REJECTED)
        self.assertIn("Unrelated data", notif.reason)

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_version_bump_notifies_only_past_downloaders(self):
        downloader = make_user("cydownloader", "cydownloader@aastu.edu.et")
        never_downloaded = make_user("cyneverdl", "cyneverdl@aastu.edu.et")
        ActivityLog.log(user=downloader, action="dataset_download", target_object=f"Dataset:{self.dataset.id}")

        route_change(dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT, submitted_by=self.owner,
                     new_file_key="v2.csv", diff_percentage=50.0, change_summary={}, proposed_metadata={})
        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        decide_pending_content_update(update, "approve", self.checker)

        self.assertTrue(Notification.objects.filter(
            user=downloader, notification_type=Notification.NotificationType.NEW_VERSION_AVAILABLE
        ).exists())
        self.assertFalse(Notification.objects.filter(
            user=never_downloaded, notification_type=Notification.NotificationType.NEW_VERSION_AVAILABLE
        ).exists())


class ApplyRevisionServiceTests(APITestCase):
    def setUp(self):
        self.owner = make_user("arowner", "arowner@aastu.edu.et")
        self.proposer = make_user("arproposer", "arproposer@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Revision DS", owner=self.owner, status=Dataset.Status.APPROVED)
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_minor_revision_applies_immediately_on_owner_approval(self):
        revision = DatasetRevision.objects.create(
            dataset=self.dataset, submitted_by=self.proposer, previous_file_key="orig.csv",
            new_file_key="minor.csv", diff_percentage=5.0, submitter_message="Fixed two typos.",
        )
        result = apply_revision(revision)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "minor.csv")
        revision.refresh_from_db()
        self.assertEqual(revision.status, DatasetRevision.Status.APPROVED)

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    def test_major_revision_held_for_reviewer_even_after_owner_approves(self):
        revision = DatasetRevision.objects.create(
            dataset=self.dataset, submitted_by=self.proposer, previous_file_key="orig.csv",
            new_file_key="major.csv", diff_percentage=60.0, submitter_message="Replaced the whole dataset.",
        )
        apply_revision(revision)
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "orig.csv")
        self.assertTrue(PendingContentUpdate.objects.filter(
            dataset=self.dataset, source=PendingContentUpdate.Source.REVISION, status="pending"
        ).exists())


# ---------------------------------------------------------------------------
# HTTP-level — permission boundaries and full endpoint flows, mocking file I/O
# ---------------------------------------------------------------------------

class ProposeRevisionPermissionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("permowner", "permowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Perm DS", owner=self.owner, status=Dataset.Status.APPROVED)
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")

    def test_public_user_cannot_propose_revision(self):
        public_user = make_user("permpublic", "permpublic@aastu.edu.et", role="public")
        self.client.force_authenticate(public_user)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/propose-revision/",
                                 {"submitter_message": "trying anyway"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_checker_cannot_propose_revision(self):
        checker = make_user("permchecker", "permchecker@aastu.edu.et", role="checker")
        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/propose-revision/",
                                 {"submitter_message": "trying anyway"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_propose_revision(self):
        """IsResearcherOnly is deliberately stricter than IsResearcherOrAdmin — admin
        doesn't get a free pass here."""
        admin = make_user("permadmin", "permadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/propose-revision/",
                                 {"submitter_message": "trying anyway"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_researcher_missing_message_is_rejected(self):
        researcher = make_user("permresearcher", "permresearcher@aastu.edu.et")
        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/propose-revision/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ProposeRevisionEndToEndTests(APITestCase):
    def setUp(self):
        self.owner = make_user("e2eowner", "e2eowner@aastu.edu.et")
        self.researcher = make_user("e2eresearcher", "e2eresearcher@aastu.edu.et")
        self.checker = make_user("e2echecker", "e2echecker@aastu.edu.et", role="checker")
        self.dataset = Dataset.objects.create(title="E2E DS", owner=self.owner, status=Dataset.Status.APPROVED)
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    @patch("apps.datasets.views.presigned_download_url")
    @patch("apps.datasets.views.compute_diff")
    @patch("apps.datasets.views.finalize_upload")
    @patch("apps.datasets.views.minio_client")
    def test_minor_revision_full_cycle_approve(self, mock_minio, mock_finalize, mock_diff, mock_presigned):
        mock_presigned.return_value = "https://fake-url/example"
        mock_finalize.return_value = DatasetFile.objects.create(
            dataset=self.dataset, file_key="new.csv", file_type="csv", file_size=10, checksum="b"
        )
        mock_diff.return_value = (5.0, {"overall_summary": "Minor edits."})

        self.client.force_authenticate(self.researcher)
        propose_resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/propose-revision/",
            {"submitter_message": "Fixed a data entry error.", "upload_session_id": "sess1", "filename": "new.csv"},
        )
        self.assertEqual(propose_resp.status_code, status.HTTP_201_CREATED)
        revision_id = propose_resp.data["id"]

        self.client.force_authenticate(self.owner)
        comparison = self.client.get(f"/api/datasets/revisions/{revision_id}/comparison/")
        self.assertEqual(comparison.status_code, status.HTTP_200_OK)
        self.assertFalse(comparison.data["will_trigger_content_review"])

        decide_resp = self.client.post(f"/api/datasets/revisions/{revision_id}/decide/", {"decision": "approve"})
        self.assertEqual(decide_resp.data["status"], "applied")
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "new.csv")

    @override_settings(VERSION_BUMP_THRESHOLD_PCT=15.0)
    @patch("apps.datasets.views.presigned_download_url")
    @patch("apps.datasets.views.compute_diff")
    @patch("apps.datasets.views.finalize_upload")
    @patch("apps.datasets.views.minio_client")
    def test_major_revision_owner_approves_then_checker_also_required(self, mock_minio, mock_finalize, mock_diff,  mock_presigned):
        mock_presigned.return_value = "https://fake-url/example"
        mock_finalize.return_value = DatasetFile.objects.create(
            dataset=self.dataset, file_key="overhauled.csv", file_type="csv", file_size=10, checksum="c"
        )
        mock_diff.return_value = (70.0, {"overall_summary": "Substantial rewrite."})

        self.client.force_authenticate(self.researcher)
        propose_resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/propose-revision/",
            {"submitter_message": "Rebuilt from a new source.", "upload_session_id": "sess2", "filename": "overhauled.csv"},
        )
        revision_id = propose_resp.data["id"]

        self.client.force_authenticate(self.owner)
        comparison = self.client.get(f"/api/datasets/revisions/{revision_id}/comparison/")
        self.assertTrue(comparison.data["will_trigger_content_review"])

        self.client.post(f"/api/datasets/revisions/{revision_id}/decide/", {"decision": "approve"})
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "orig.csv")  # owner alone isn't enough

        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        self.client.force_authenticate(self.checker)
        review_resp = self.client.post(f"/api/admin-panel/content-updates/{update.id}/decide/", {"decision": "approve"})
        self.assertEqual(review_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "overhauled.csv")

    @patch("apps.datasets.views.compute_diff")
    @patch("apps.datasets.views.finalize_upload")
    @patch("apps.datasets.views.minio_client")
    def test_major_revision_checker_rejects_stays_unapplied(self, mock_minio, mock_finalize, mock_diff):
        mock_finalize.return_value = DatasetFile.objects.create(
            dataset=self.dataset, file_key="bad.csv", file_type="csv", file_size=10, checksum="d"
        )
        mock_diff.return_value = (70.0, {})

        self.client.force_authenticate(self.researcher)
        propose_resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/propose-revision/",
            {"submitter_message": "Big change.", "upload_session_id": "sess3", "filename": "bad.csv"},
        )
        revision_id = propose_resp.data["id"]

        self.client.force_authenticate(self.owner)
        self.client.post(f"/api/datasets/revisions/{revision_id}/decide/", {"decision": "approve"})

        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        self.client.force_authenticate(self.checker)
        reject_resp = self.client.post(f"/api/admin-panel/content-updates/{update.id}/decide/",
                                        {"decision": "reject", "reason": "Data provenance unclear."})
        self.assertEqual(reject_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "orig.csv")

        self.assertTrue(Notification.objects.filter(
            user=self.researcher, notification_type=Notification.NotificationType.REVISION_REJECTED
        ).exists())

    def test_owner_rejection_requires_reason(self):
        revision = DatasetRevision.objects.create(
            dataset=self.dataset, submitted_by=self.researcher, previous_file_key="orig.csv",
            new_file_key="x.csv", diff_percentage=10.0, submitter_message="Some change.",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/datasets/revisions/{revision.id}/decide/", {"decision": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_rejection_with_reason_notifies_submitter(self):
        revision = DatasetRevision.objects.create(
            dataset=self.dataset, submitted_by=self.researcher, previous_file_key="orig.csv",
            new_file_key="x.csv", diff_percentage=10.0, submitter_message="Some change.",
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/datasets/revisions/{revision.id}/decide/",
                                 {"decision": "reject", "reason": "Not aligned with the dataset's scope."})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        revision.refresh_from_db()
        self.assertEqual(revision.status, "rejected")
        self.assertTrue(Notification.objects.filter(
            user=self.researcher, notification_type=Notification.NotificationType.REVISION_REJECTED
        ).exists())

    def test_non_owner_cannot_decide_revision(self):
        revision = DatasetRevision.objects.create(
            dataset=self.dataset, submitted_by=self.researcher, previous_file_key="orig.csv",
            new_file_key="x.csv", diff_percentage=10.0, submitter_message="Some change.",
        )
        outsider = make_user("e2eoutsider", "e2eoutsider@aastu.edu.et")
        self.client.force_authenticate(outsider)
        resp = self.client.post(f"/api/datasets/revisions/{revision.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class DirectEditPermissionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("depowner", "depowner@aastu.edu.et")
        self.researcher_contributor = make_user("depresearchercontrib", "depresearchercontrib@aastu.edu.et")
        self.public_contributor = make_user("deppubliccontrib", "deppubliccontrib@aastu.edu.et", role="public")
        self.non_contributor_researcher = make_user("depnoncontrib", "depnoncontrib@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Direct Edit DS", owner=self.owner)

        Contributor.objects.create(dataset=self.dataset, user=self.researcher_contributor,
                                    name="Researcher Contrib", contributor_type="contributor")
        Contributor.objects.create(dataset=self.dataset, user=self.public_contributor,
                                    name="Public Contrib", contributor_type="contributor")

    def test_owner_can_edit_directly(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/", {"title": "Renamed by Owner"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_researcher_contributor_can_edit_directly(self):
        self.client.force_authenticate(self.researcher_contributor)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/", {"title": "Renamed by Researcher Contributor"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_public_role_contributor_cannot_edit_despite_being_a_contributor(self):
        """Being credited as a contributor skips the 'ask first' step, not the
        'must be a researcher' requirement."""
        self.client.force_authenticate(self.public_contributor)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/", {"title": "Should Still Fail"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_contributor_researcher_cannot_edit(self):
        self.client.force_authenticate(self.non_contributor_researcher)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/", {"title": "Should Fail"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_edit(self):
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/", {"title": "Should Fail"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("apps.datasets.views.compute_diff")
    @patch("apps.datasets.views.finalize_upload")
    @patch("apps.datasets.views.minio_client")
    def test_researcher_contributor_major_edit_still_routes_through_reviewer(self, mock_minio, mock_finalize, mock_diff):
        """Confirms contributors skip owner-approval but NOT the reviewer gate."""
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")
        mock_finalize.return_value = DatasetFile.objects.create(
            dataset=self.dataset, file_key="contrib_new.csv", file_type="csv", file_size=10, checksum="d"
        )
        mock_diff.return_value = (80.0, {})

        self.client.force_authenticate(self.researcher_contributor)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/",
                                  {"upload_session_id": "s", "filename": "contrib_new.csv"})
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(resp.data["status"], "pending_review")

        update = PendingContentUpdate.objects.get(dataset=self.dataset)
        self.assertEqual(update.source, PendingContentUpdate.Source.CONTRIBUTOR_EDIT)
        self.assertEqual(update.submitted_by, self.researcher_contributor)
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "orig.csv")  # not applied yet

    @patch("apps.datasets.views.compute_diff")
    @patch("apps.datasets.views.finalize_upload")
    @patch("apps.datasets.views.minio_client")
    def test_owner_minor_edit_applies_immediately(self, mock_minio, mock_finalize, mock_diff):
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")
        mock_finalize.return_value = DatasetFile.objects.create(
            dataset=self.dataset, file_key="owner_minor.csv", file_type="csv", file_size=10, checksum="e"
        )
        mock_diff.return_value = (3.0, {})

        self.client.force_authenticate(self.owner)
        resp = self.client.patch(f"/api/datasets/{self.dataset.id}/update/",
                                  {"upload_session_id": "s2", "filename": "owner_minor.csv"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], "applied")
        self.assertEqual(self.dataset.files.latest("uploaded_at").file_key, "owner_minor.csv")


class ContentUpdateQueuePermissionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("cqowner", "cqowner@aastu.edu.et")
        self.checker = make_user("cqchecker", "cqchecker@aastu.edu.et", role="checker")
        self.admin = make_user("cqadmin", "cqadmin@aastu.edu.et", role="admin")
        self.researcher = make_user("cqresearcher", "cqresearcher@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Queue DS", owner=self.owner)
        DatasetFile.objects.create(dataset=self.dataset, file_key="orig.csv", file_type="csv", file_size=10, checksum="a")
        self.update = PendingContentUpdate.objects.create(
            dataset=self.dataset, source=PendingContentUpdate.Source.OWNER_EDIT,
            submitted_by=self.owner, new_file_key="new.csv", diff_percentage=40.0,
        )

    def test_researcher_cannot_view_queue(self):
        self.client.force_authenticate(self.researcher)
        resp = self.client.get("/api/admin-panel/content-updates/queue/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_checker_can_view_queue(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.get("/api/admin-panel/content-updates/queue/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_admin_can_view_queue(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get("/api/admin-panel/content-updates/queue/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_researcher_cannot_decide(self):
        self.client.force_authenticate(self.researcher)
        resp = self.client.post(f"/api/admin-panel/content-updates/{self.update.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_reject_without_reason_is_blocked(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.post(f"/api/admin-panel/content-updates/{self.update.id}/decide/", {"decision": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)