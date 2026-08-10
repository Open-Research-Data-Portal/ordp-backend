from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from .models import DatasetDeletionRequest, DeletionRequestVote


def make_dataset(owner, title="Test DS"):
    return Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.APPROVED)


class RequestDeletionTests(APITestCase):
    def test_checker_can_request_deletion(self):
        owner = make_user("delowner", "delowner@aastu.edu.et")
        checker = make_user("delchecker", "delchecker@aastu.edu.et", role="checker")
        dataset = make_dataset(owner)

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/request-deletion/", {"reason": "Contains PII."})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(DatasetDeletionRequest.objects.filter(dataset=dataset, requested_by=checker).exists())

    def test_reason_required(self):
        owner = make_user("delowner2", "delowner2@aastu.edu.et")
        checker = make_user("delchecker2", "delchecker2@aastu.edu.et", role="checker")
        dataset = make_dataset(owner)

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/request-deletion/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_researcher_cannot_request_deletion(self):
        owner = make_user("delowner3", "delowner3@aastu.edu.et")
        researcher = make_user("delresearcher", "delresearcher@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/request-deletion/", {"reason": "test"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class DeletionVotingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("dvowner", "dvowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner, "Deletion Vote DS")
        self.requester = make_user("dvrequester", "dvrequester@aastu.edu.et", role="checker")
        self.reviewers = [
            make_user(f"dvreviewer{i}", f"dvreviewer{i}@aastu.edu.et", role="checker") for i in range(3)
        ]
        self.admin = make_user("dvadmin", "dvadmin@aastu.edu.et", role="admin")

        self.client.force_authenticate(self.requester)
        resp = self.client.post(f"/api/admin-panel/datasets/{self.dataset.id}/request-deletion/", {"reason": "Duplicate upload."})
        self.request_id = resp.data["request_id"]

    def test_stays_pending_below_quorum(self):
        self.client.force_authenticate(self.reviewers[0])
        resp = self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "approve"})
        self.assertEqual(resp.data["status"], "pending")

    def test_majority_approve_does_not_auto_delete(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "approve"})

        deletion_request = DatasetDeletionRequest.objects.get(id=self.request_id)
        self.assertEqual(deletion_request.status, DatasetDeletionRequest.Status.APPROVED)
        self.assertTrue(Dataset.objects.filter(id=self.dataset.id).exists())  # still there — not auto-deleted

    def test_majority_reject_blocks_deletion(self):
        for i, reviewer in enumerate(self.reviewers):
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/",
                              {"vote": "approve" if i == 0 else "reject"})
        deletion_request = DatasetDeletionRequest.objects.get(id=self.request_id)
        self.assertEqual(deletion_request.status, DatasetDeletionRequest.Status.REJECTED)

    def test_reviewer_can_change_vote(self):
        self.client.force_authenticate(self.reviewers[0])
        self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "reject"})
        self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "approve"})
        deletion_request = DatasetDeletionRequest.objects.get(id=self.request_id)
        self.assertEqual(deletion_request.votes.count(), 1)
        self.assertEqual(deletion_request.votes.first().vote, "approve")

    def test_resolved_request_rejects_further_votes(self):
        for reviewer in self.reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "approve"})
        late_reviewer = make_user("dvlate", "dvlate@aastu.edu.et", role="checker")
        self.client.force_authenticate(late_reviewer)
        resp = self.client.post(f"/api/admin-panel/deletion-requests/{self.request_id}/vote/", {"vote": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

class ExecuteDeletionTests(APITestCase):
    def _approved_request(self):
        owner = make_user("exowner", "exowner@aastu.edu.et")
        dataset = make_dataset(owner, "Execute DS")
        requester = make_user("exrequester", "exrequester@aastu.edu.et", role="checker")
        reviewers = [make_user(f"exreviewer{i}", f"exreviewer{i}@aastu.edu.et", role="checker") for i in range(3)]

        self.client.force_authenticate(requester)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/request-deletion/", {"reason": "test"})
        request_id = resp.data["request_id"]
        for reviewer in reviewers:
            self.client.force_authenticate(reviewer)
            self.client.post(f"/api/admin-panel/deletion-requests/{request_id}/vote/", {"vote": "approve"})
        return dataset, request_id

    def test_admin_can_execute_approved_deletion(self):
        dataset, request_id = self._approved_request()
        admin = make_user("exadmin", "exadmin@aastu.edu.et", role="admin")

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/deletion-requests/{request_id}/execute/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(Dataset.objects.filter(id=dataset.id).exists())

    def test_checker_cannot_execute(self):
        dataset, request_id = self._approved_request()
        checker = make_user("exchecker2", "exchecker2@aastu.edu.et", role="checker")

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/deletion-requests/{request_id}/execute/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_execute_pending_request(self):
        owner = make_user("exowner2", "exowner2@aastu.edu.et")
        dataset = make_dataset(owner, "Still Pending DS")
        requester = make_user("exrequester2", "exrequester2@aastu.edu.et", role="checker")
        admin = make_user("exadmin2", "exadmin2@aastu.edu.et", role="admin")

        self.client.force_authenticate(requester)
        resp = self.client.post(f"/api/admin-panel/datasets/{dataset.id}/request-deletion/", {"reason": "test"})
        request_id = resp.data["request_id"]

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/deletion-requests/{request_id}/execute/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)