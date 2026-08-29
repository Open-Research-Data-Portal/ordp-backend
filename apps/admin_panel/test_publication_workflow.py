from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.metadata.models import Category, Metadata, Language
from .models import DatasetReviewerAssignment


def make_submittable_dataset(owner, title='Workflow DS'):
    dataset = Dataset.objects.create(
        title=title,
        owner=owner,
        status=Dataset.Status.DRAFT,
    )

    category = Category.objects.create(
        name=f'{title} Cat',
        status=Category.Status.APPROVED,
    )

    Metadata.objects.create(
        dataset=dataset,
        description='test',
        category=category,
    )

    language = Language.objects.create(
        name=f'{title} Lang',
        status=Language.Status.APPROVED,
    )

    dataset.languages.add(language)

    return dataset


def assign_three_reviewers(dataset, prefix):
    reviewers = [
        make_user(
            f'{prefix}reviewer{i}',
            f'{prefix}reviewer{i}@aastu.edu.et',
            role='reviewer',
        )
        for i in range(3)
    ]

    for reviewer in reviewers:
        DatasetReviewerAssignment.objects.create(
            dataset=dataset,
            reviewer=reviewer,
        )

    dataset.assigned_reviewer = reviewers[0]
    dataset.save(update_fields=['assigned_reviewer'])

    return reviewers


class ModerationDecisionTests(APITestCase):
    def test_first_approval_keeps_dataset_pending(self):
        owner = make_user(
            'pwowner',
            'pwowner@aastu.edu.et',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'pw',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(reviewers[0])

        resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {'decision': 'approved'},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.PENDING,
        )

    def test_majority_approval_sets_published_status(self):
        owner = make_user(
            'mwowner',
            'mwowner@aastu.edu.et',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'mw',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        for reviewer in reviewers[:2]:
            self.client.force_authenticate(reviewer)

            resp = self.client.post(
                f'/api/admin-panel/{dataset.id}/decide/',
                {'decision': 'approved'},
            )

            self.assertEqual(
                resp.status_code,
                status.HTTP_200_OK,
            )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.PUBLISHED,
        )

        self.assertEqual(
            dataset.moderation_decisions.count(),
            2,
        )

    def test_changes_requested_sets_status_and_requires_reason(self):
        owner = make_user(
            'cwowner',
            'cwowner@aastu.edu.et',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'cw',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(reviewers[0])

        no_reason_resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {'decision': 'changes_requested'},
        )

        self.assertEqual(
            no_reason_resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {
                'decision': 'changes_requested',
                'reason': 'Please add a data source citation.',
            },
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.PENDING,
        )

        self.client.force_authenticate(reviewers[1])

        second_resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {
                'decision': 'changes_requested',
                'reason': 'Please add a data source citation.',
            },
        )

        self.assertEqual(
            second_resp.status_code,
            status.HTTP_200_OK,
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.CHANGES_REQUESTED,
        )

    def test_reject_still_requires_reason(self):
        owner = make_user(
            'rwowner',
            'rwowner@aastu.edu.et',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'rw',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(reviewers[0])

        resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {'decision': 'rejected'},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_invalid_decision_rejected(self):
        owner = make_user(
            'iwowner',
            'iwowner@aastu.edu.et',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'iw',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(reviewers[0])

        resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {'decision': 'maybe_later'},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )


class ResubmissionAfterChangesRequestedTests(APITestCase):
    def test_owner_can_resubmit_after_changes_requested(self):
        owner = make_user(
            'resowner',
            'resowner@aastu.edu.et',
            role='researcher',
        )

        dataset = make_submittable_dataset(owner)

        reviewers = assign_three_reviewers(
            dataset,
            'res',
        )

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(reviewers[0])

        self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {
                'decision': 'changes_requested',
                'reason': 'Fix the description.',
            },
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.PENDING,
        )

        self.client.force_authenticate(reviewers[1])

        second_resp = self.client.post(
            f'/api/admin-panel/{dataset.id}/decide/',
            {
                'decision': 'changes_requested',
                'reason': 'Fix the description.',
            },
        )

        self.assertEqual(
            second_resp.status_code,
            status.HTTP_200_OK,
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.CHANGES_REQUESTED,
        )

        self.client.force_authenticate(owner)

        resp = self.client.post(
            f'/api/datasets/{dataset.id}/submit/',
            {'terms_accepted': True},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
        )

        dataset.refresh_from_db()

        self.assertEqual(
            dataset.status,
            Dataset.Status.PENDING,
        )

    def test_cannot_resubmit_a_published_dataset(self):
        owner = make_user(
            'psowner',
            'psowner@aastu.edu.et',
            role='researcher',
        )

        dataset = make_submittable_dataset(owner)

        dataset.status = Dataset.Status.PUBLISHED
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(owner)

        resp = self.client.post(
            f'/api/datasets/{dataset.id}/submit/',
            {'terms_accepted': True},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_cannot_submit_an_already_pending_dataset(self):
        owner = make_user(
            'apowner',
            'apowner@aastu.edu.et',
            role='researcher',
        )

        dataset = make_submittable_dataset(owner)

        dataset.status = Dataset.Status.PENDING
        dataset.save(update_fields=['status'])

        self.client.force_authenticate(owner)

        resp = self.client.post(
            f'/api/datasets/{dataset.id}/submit/',
            {'terms_accepted': True},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
