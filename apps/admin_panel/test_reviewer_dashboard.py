from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from .models import ModerationDecision


def make_dataset(owner, title="Test DS", status=Dataset.Status.PENDING, assigned_reviewer=None):
    return Dataset.objects.create(title=title, owner=owner, status=status, assigned_reviewer=assigned_reviewer)


class ReviewerOverviewTests(APITestCase):
    def test_counts_only_datasets_assigned_to_this_reviewer(self):
        owner = make_user("rowowner", "rowowner@aastu.edu.et")
        checker = make_user("rowchecker", "rowchecker@aastu.edu.et", role="checker")
        other_checker = make_user("rowother", "rowother@aastu.edu.et", role="checker")

        make_dataset(owner, "Assigned To Me", assigned_reviewer=checker)
        make_dataset(owner, "Assigned To Other", assigned_reviewer=other_checker)
        make_dataset(owner, "Unassigned")

        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/overview/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["assigned_datasets_pending"], 1)

    def test_public_role_cannot_access_overview(self):
        public_user = make_user("rowpublic", "rowpublic@aastu.edu.et", role="public")
        self.client.force_authenticate(public_user)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/overview/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_access_requests_awaiting_my_vote_excludes_already_voted(self):
        from apps.datasets.factories import make_user as _mu
        from apps.datasets.models import Dataset as _DS
        from apps.sharing.models import DatasetAccessRequest, AccessRequestVote, UsabilityFormResponse, RestrictedAccessJustification

        owner = make_user("rowowner2", "rowowner2@aastu.edu.et")
        checker = make_user("rowchecker2", "rowchecker2@aastu.edu.et", role="checker")
        requester = make_user("rowrequester", "rowrequester@aastu.edu.et")
        dataset = make_dataset(owner, "Access DS", status=Dataset.Status.APPROVED)

        usability = UsabilityFormResponse.objects.create(dataset=dataset, user=requester, purpose="research")
        justification = RestrictedAccessJustification.objects.create(dataset=dataset, requester=requester, justification="need it")
        access_request_voted = DatasetAccessRequest.objects.create(
            dataset=dataset, requester=requester, usability_form=usability, restricted_justification=justification,
        )
        access_request_unvoted = DatasetAccessRequest.objects.create(
            dataset=dataset, requester=requester, usability_form=usability, restricted_justification=justification,
        )
        AccessRequestVote.objects.create(access_request=access_request_voted, reviewer=checker, vote="approve")

        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/overview/")
        self.assertEqual(resp.data["access_requests_awaiting_my_vote"], 1)


class ReviewerMetricsTests(APITestCase):
    def test_metrics_reflect_only_this_reviewers_decisions(self):
        owner = make_user("rmowner", "rmowner@aastu.edu.et")
        checker = make_user("rmchecker", "rmchecker@aastu.edu.et", role="checker")
        other_checker = make_user("rmother", "rmother@aastu.edu.et", role="checker")
        ds1 = make_dataset(owner, "RM DS 1")
        ds2 = make_dataset(owner, "RM DS 2")
        ds3 = make_dataset(owner, "RM DS 3")

        ModerationDecision.objects.create(dataset=ds1, reviewer=checker, decision="approved")
        ModerationDecision.objects.create(dataset=ds2, reviewer=checker, decision="rejected", reason="bad data")
        ModerationDecision.objects.create(dataset=ds3, reviewer=other_checker, decision="approved")

        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/metrics/")
        self.assertEqual(resp.data["total_reviewed"], 2)
        self.assertEqual(resp.data["total_approved"], 1)
        self.assertEqual(resp.data["total_rejected"], 1)


class ReviewerGuidelinesTests(APITestCase):
    def test_guidelines_accessible_to_checker(self):
        checker = make_user("rgchecker", "rgchecker@aastu.edu.et", role="checker")
        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/guidelines/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("moderation_guidelines", resp.data)
        self.assertIn("sharing_committee_quorum", resp.data)

    def test_researcher_cannot_access_guidelines(self):
        researcher = make_user("rgresearcher", "rgresearcher@aastu.edu.et", role="researcher")
        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/admin-panel/dashboard/reviewer/guidelines/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)