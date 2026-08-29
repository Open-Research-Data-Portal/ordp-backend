from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.metadata.models import Category, Metadata
from apps.datasets.models import Dataset
from apps.datasets.factories import make_user
from apps.notifications.models import Notification
from .models import DatasetReviewerAssignment


User = get_user_model()


class ModerationTests(APITestCase):
    def setUp(self):
        self.owner = make_user(
            'modowner',
            'modowner@aastu.edu.et',
            'researcher',
        )

        self.reviewers = [
            make_user(
                f'modreviewer{i}',
                f'modreviewer{i}@aastu.edu.et',
                'reviewer',
            )
            for i in range(3)
        ]

        self.researcher = make_user(
            'plainresearcher',
            'plain@aastu.edu.et',
            'researcher',
        )

        self.category = Category.objects.create(name='Health')

        for reviewer in self.reviewers:
            reviewer.profile.interests.add(self.category)

        self.dataset = Dataset.objects.create(
            title='Under Review',
            owner=self.owner,
            status=Dataset.Status.PENDING,
        )

        Metadata.objects.create(
            dataset=self.dataset,
            description='d',
            category=self.category,
        )

        for reviewer in self.reviewers:
            DatasetReviewerAssignment.objects.create(
                dataset=self.dataset,
                reviewer=reviewer,
            )

        self.dataset.assigned_reviewer = self.reviewers[0]
        self.dataset.save(update_fields=['assigned_reviewer'])

    def test_researcher_cannot_access_moderation_queue(self):
        self.client.force_authenticate(self.researcher)

        resp = self.client.get('/api/admin-panel/queue/')

        self.assertEqual(
            resp.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_checker_sees_pending_dataset_in_queue(self):
        self.client.force_authenticate(self.reviewers[0])

        resp = self.client.get('/api/admin-panel/queue/')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_unassigned_reviewer_cannot_vote(self):
        outsider = make_user(
            'outsidereviewer',
            'outsidereviewer@aastu.edu.et',
            'reviewer',
        )

        self.client.force_authenticate(outsider)

        resp = self.client.post(
            f'/api/admin-panel/{self.dataset.id}/decide/',
            {'decision': 'approved'},
        )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_reject_requires_reason(self):
        self.client.force_authenticate(self.reviewers[0])

        resp = self.client.post(
            f'/api/admin-panel/{self.dataset.id}/decide/',
            {'decision': 'rejected'},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_first_approval_does_not_publish(self):
        self.client.force_authenticate(self.reviewers[0])

        resp = self.client.post(
            f'/api/admin-panel/{self.dataset.id}/decide/',
            {'decision': 'approved'},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.dataset.refresh_from_db()

        self.assertEqual(
            self.dataset.status,
            Dataset.Status.PENDING,
        )

    def test_majority_approval_publishes_immediately(self):
        for reviewer in self.reviewers[:2]:
            self.client.force_authenticate(reviewer)

            resp = self.client.post(
                f'/api/admin-panel/{self.dataset.id}/decide/',
                {'decision': 'approved'},
            )

            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.dataset.refresh_from_db()

        self.assertEqual(
            self.dataset.status,
            Dataset.Status.PUBLISHED,
        )

        self.assertTrue(
            Notification.objects.filter(
                user=self.owner,
                notification_type=Notification.NotificationType.DATASET_APPROVED,
            ).exists()
        )

        self.assertEqual(
            self.dataset.moderation_decisions.count(),
            2,
        )

    def test_reject_with_reason_notifies_owner(self):
        for reviewer in self.reviewers[:2]:
            self.client.force_authenticate(reviewer)

            resp = self.client.post(
                f'/api/admin-panel/{self.dataset.id}/decide/',
                {
                    'decision': 'rejected',
                    'reason': 'Missing consent documentation.',
                },
            )

            self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.dataset.refresh_from_db()

        self.assertEqual(
            self.dataset.status,
            Dataset.Status.REJECTED,
        )

        notif = Notification.objects.get(
            user=self.owner,
            notification_type=Notification.NotificationType.DATASET_REJECTED,
        )

        self.assertIn(
            'Missing consent documentation.',
            notif.reason,
        )

    def test_reviewer_cannot_vote_twice(self):
        reviewer = self.reviewers[0]

        self.client.force_authenticate(reviewer)

        first = self.client.post(
            f'/api/admin-panel/{self.dataset.id}/decide/',
            {'decision': 'approved'},
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.post(
            f'/api/admin-panel/{self.dataset.id}/decide/',
            {'decision': 'approved'},
        )

        self.assertEqual(
            second.status_code,
            status.HTTP_400_BAD_REQUEST,
        )
