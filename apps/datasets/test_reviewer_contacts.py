from rest_framework import status
from rest_framework.test import APITestCase

from apps.admin_panel.models import DatasetReviewerAssignment
from apps.datasets.factories import make_user
from apps.datasets.models import Dataset


def make_dataset(owner, title="Reviewer Contact DS", status=Dataset.Status.PENDING):
    return Dataset.objects.create(title=title, owner=owner, status=status)


class DatasetReviewerContactTests(APITestCase):
    def test_owner_can_see_assigned_reviewer_contact_details(self):
        owner = make_user("contactowner", "contactowner@aastu.edu.et", role="researcher")
        reviewer = make_user("contactreviewer", "contactreviewer@aastu.edu.et", role="reviewer")
        dataset = make_dataset(owner)
        assignment = DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=reviewer)

        self.client.force_authenticate(owner)
        resp = self.client.get(f"/api/datasets/{dataset.id}/reviewers/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["dataset_id"], str(dataset.id))
        self.assertEqual(len(resp.data["reviewers"]), 1)
        self.assertEqual(resp.data["reviewers"][0]["id"], reviewer.id)
        self.assertEqual(resp.data["reviewers"][0]["email"], "contactreviewer@aastu.edu.et")
        self.assertEqual(resp.data["reviewers"][0]["contact_email"], "contactreviewer@aastu.edu.et")
        self.assertEqual(resp.data["reviewers"][0]["contact_url"], "mailto:contactreviewer@aastu.edu.et")
        self.assertEqual(resp.data["reviewers"][0]["assigned_at"], assignment.assigned_at)

    def test_unrelated_researcher_cannot_see_reviewer_contacts(self):
        owner = make_user("contactowner2", "contactowner2@aastu.edu.et", role="researcher")
        other = make_user("contactother", "contactother@aastu.edu.et", role="researcher")
        reviewer = make_user("contactreviewer2", "contactreviewer2@aastu.edu.et", role="reviewer")
        dataset = make_dataset(owner)
        DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=reviewer)

        self.client.force_authenticate(other)
        resp = self.client.get(f"/api/datasets/{dataset.id}/reviewers/")

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_see_dataset_reviewer_contacts(self):
        owner = make_user("contactowner3", "contactowner3@aastu.edu.et", role="researcher")
        admin = make_user("contactadmin", "contactadmin@aastu.edu.et", role="admin")
        reviewer = make_user("contactreviewer3", "contactreviewer3@aastu.edu.et", role="reviewer")
        dataset = make_dataset(owner)
        DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=reviewer)

        self.client.force_authenticate(admin)
        resp = self.client.get(f"/api/datasets/{dataset.id}/reviewers/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in resp.data["reviewers"]], [reviewer.id])
