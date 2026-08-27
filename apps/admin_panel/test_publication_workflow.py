from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.metadata.models import Category, Subject, Metadata, Language
from .models import ModerationDecision


def make_submittable_dataset(owner, title="Workflow DS"):
    dataset = Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.DRAFT)
    category = Category.objects.create(name=f"{title} Cat", status=Category.Status.APPROVED)
    subject = Subject.objects.create(name=f"{title} Subj")
    Metadata.objects.create(dataset=dataset, description="test", category=category, subject=subject)
    language = Language.objects.create(name=f"{title} Lang", status=Language.Status.APPROVED)
    dataset.languages.add(language)
    return dataset


class ModerationDecisionTests(APITestCase):
    def test_approve_sets_published_status(self):
        owner = make_user("pwowner", "pwowner@aastu.edu.et")
        checker = make_user("pwchecker", "pwchecker@aastu.edu.et", role="reviewer")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/{dataset.id}/decide/", {"decision": "approved"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.Status.PUBLISHED)
        self.assertEqual(dataset.status, Dataset.Status.APPROVED)  

    def test_changes_requested_sets_status_and_requires_reason(self):
        owner = make_user("cwowner", "cwowner@aastu.edu.et")
        checker = make_user("cwchecker", "cwchecker@aastu.edu.et", role="reviewer")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(checker)
        no_reason_resp = self.client.post(f"/api/admin-panel/{dataset.id}/decide/", {"decision": "changes_requested"})
        self.assertEqual(no_reason_resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.post(f"/api/admin-panel/{dataset.id}/decide/",
                                 {"decision": "changes_requested", "reason": "Please add a data source citation."})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.Status.CHANGES_REQUESTED)

    def test_reject_still_requires_reason(self):
        owner = make_user("rwowner", "rwowner@aastu.edu.et")
        checker = make_user("rwchecker", "rwchecker@aastu.edu.et", role="reviewer")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/{dataset.id}/decide/", {"decision": "rejected"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_decision_rejected(self):
        owner = make_user("iwowner", "iwowner@aastu.edu.et")
        checker = make_user("iwchecker", "iwchecker@aastu.edu.et", role="reviewer")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(checker)
        resp = self.client.post(f"/api/admin-panel/{dataset.id}/decide/", {"decision": "maybe_later"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ResubmissionAfterChangesRequestedTests(APITestCase):
    def test_owner_can_resubmit_after_changes_requested(self):
        owner = make_user("resowner", "resowner@aastu.edu.et", role="researcher")
        checker = make_user("reschecker", "reschecker@aastu.edu.et", role="reviewer")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(checker)
        self.client.post(f"/api/admin-panel/{dataset.id}/decide/",
                          {"decision": "changes_requested", "reason": "Fix the description."})
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.Status.CHANGES_REQUESTED)

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.Status.PENDING)

    def test_cannot_resubmit_a_published_dataset(self):
        owner = make_user("psowner", "psowner@aastu.edu.et", role="researcher")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PUBLISHED
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_submit_an_already_pending_dataset(self):
        owner = make_user("apowner", "apowner@aastu.edu.et", role="researcher")
        dataset = make_submittable_dataset(owner)
        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=["status"])

        self.client.force_authenticate(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)