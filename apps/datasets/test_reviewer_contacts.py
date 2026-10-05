from rest_framework import status
from rest_framework.test import APITestCase
from django.core import mail
from django.test import override_settings

from apps.admin_panel.models import DatasetReviewerAssignment
from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.metadata.models import Category, Language, Metadata


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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_submit_assigns_available_reviewers_and_emails_them(self):
        owner = make_user("contactsubmitowner", "contactsubmitowner@aastu.edu.et", role="researcher")
        reviewer1 = make_user("contactsubmitreviewer1", "contactsubmitreviewer1@aastu.edu.et", role="reviewer")
        reviewer2 = make_user("contactsubmitreviewer2", "contactsubmitreviewer2@aastu.edu.et", role="reviewer")
        reviewer3 = make_user("contactsubmitreviewer3", "contactsubmitreviewer3@aastu.edu.et", role="reviewer")
        dataset = make_dataset(owner, title="Submit Email DS", status=Dataset.Status.DRAFT)
        category = Category.objects.create(name="Submit Email Cat")
        language = Language.objects.create(name="Submit Email Lang")
        metadata = Metadata.objects.create(dataset=dataset, description="test", category=category)
        metadata.languages.add(language)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(DatasetReviewerAssignment.objects.filter(dataset=dataset).count(), 3)
        recipients = sorted(message.to[0] for message in mail.outbox)
        self.assertEqual(
            recipients,
            sorted([reviewer1.email, reviewer2.email, reviewer3.email]),
        )
