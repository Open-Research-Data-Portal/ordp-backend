from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.metadata.models import Category, Subject, Metadata
from .models import Dataset, Contributor, DatasetRevision


def make_dataset(owner, title="Test DS", visibility=Dataset.Visibility.PUBLIC, status=Dataset.Status.APPROVED):
    return Dataset.objects.create(title=title, owner=owner, visibility=visibility, status=status)


class DashboardStatsTests(APITestCase):
    def test_stats_reflect_owned_and_coowned_datasets(self):
        owner = make_user("dsowner", "dsowner@aastu.edu.et", role="researcher")
        coowner_user = make_user("dscoowner", "dscoowner@aastu.edu.et", role="researcher")
        dataset1 = make_dataset(owner, "DS One")
        dataset2 = make_dataset(owner, "DS Two")
        Contributor.objects.create(
            dataset=dataset2, user=coowner_user, name="CoOwner", contributor_type=Contributor.ContributorType.OWNER
        )
        Dataset.objects.filter(id=dataset1.id).update(view_count=10, download_count=3)
        Dataset.objects.filter(id=dataset2.id).update(view_count=5, download_count=1)

        self.client.force_authenticate(coowner_user)
        resp = self.client.get("/api/datasets/dashboard/stats/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_datasets"], 1)  # only co-owned one, not dataset1
        self.assertEqual(resp.data["total_views_received"], 5)
        self.assertEqual(resp.data["total_downloads_received"], 1)

    def test_most_viewed_dataset_reported(self):
        owner = make_user("dsowner2", "dsowner2@aastu.edu.et", role="researcher")
        low = make_dataset(owner, "Low Views")
        high = make_dataset(owner, "High Views")
        Dataset.objects.filter(id=low.id).update(view_count=2)
        Dataset.objects.filter(id=high.id).update(view_count=50)

        self.client.force_authenticate(owner)
        resp = self.client.get("/api/datasets/dashboard/stats/")
        self.assertEqual(resp.data["most_viewed_dataset"]["title"], "High Views")

    def test_public_role_cannot_access_dashboard(self):
        public_user = make_user("dspublic", "dspublic@aastu.edu.et", role="public")
        self.client.force_authenticate(public_user)
        resp = self.client.get("/api/datasets/dashboard/stats/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class RecentActivityTests(APITestCase):
    def test_download_by_someone_else_appears_in_my_activity(self):
        owner = make_user("raowner", "raowner@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "RA DS")
        from apps.accounts.models import ActivityLog
        stranger = make_user("rastranger", "rastranger@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(
            user=stranger, action="dataset_download", target_object=f"Dataset:{dataset.id}",
            ip_address="127.0.0.1",
        )

        self.client.force_authenticate(owner)
        resp = self.client.get("/api/datasets/dashboard/recent-activity/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["action"], "dataset_download")

    def test_my_own_actions_excluded_from_my_activity(self):
        owner = make_user("raowner2", "raowner2@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "RA DS 2")
        from apps.accounts.models import ActivityLog
        ActivityLog.objects.create(
            user=owner, action="owner_download", target_object=f"Dataset:{dataset.id}", ip_address="127.0.0.1",
        )

        self.client.force_authenticate(owner)
        resp = self.client.get("/api/datasets/dashboard/recent-activity/")
        self.assertEqual(len(resp.data), 0)


class FeedTests(APITestCase):
    def setUp(self):
        self.category = Category.objects.create(name="Agriculture")
        self.other_category = Category.objects.create(name="Health")
        self.subject = Subject.objects.create(name="Crop Yield")

    def _dataset_with_category(self, owner, title, category, visibility=Dataset.Visibility.PUBLIC):
        dataset = make_dataset(owner, title, visibility=visibility)
        Metadata.objects.create(dataset=dataset, description="test", category=category, subject=self.subject)
        return dataset

    def test_feed_includes_all_visibility_tiers_matching_interest(self):
        researcher = make_user("feedresearcher", "feedresearcher@aastu.edu.et", role="researcher")
        researcher.profile.interests.set([self.category])
        other_owner = make_user("feedowner", "feedowner@aastu.edu.et", role="researcher")

        public_ds = self._dataset_with_category(other_owner, "Public Ag DS", self.category, Dataset.Visibility.PUBLIC)
        restricted_ds = self._dataset_with_category(other_owner, "Restricted Ag DS", self.category, Dataset.Visibility.RESTRICTED)
        unrelated_ds = self._dataset_with_category(other_owner, "Health DS", self.other_category, Dataset.Visibility.PUBLIC)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/datasets/dashboard/feed/")
        titles = {d["title"] for d in resp.data}
        self.assertIn("Public Ag DS", titles)
        self.assertIn("Restricted Ag DS", titles)
        self.assertNotIn("Health DS", titles)

    def test_own_dataset_excluded_from_own_feed(self):
        researcher = make_user("feedresearcher2", "feedresearcher2@aastu.edu.et", role="researcher")
        researcher.profile.interests.set([self.category])
        own_ds = self._dataset_with_category(researcher, "My Own Ag DS", self.category)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/datasets/dashboard/feed/")
        titles = {d["title"] for d in resp.data}
        self.assertNotIn("My Own Ag DS", titles)


class MyContributionsTests(APITestCase):
    def test_applied_revision_on_others_dataset_appears(self):
        owner = make_user("mcowner", "mcowner@aastu.edu.et", role="researcher")
        contributor_user = make_user("mccontrib", "mccontrib@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "MC DS")
        DatasetRevision.objects.create(
            dataset=dataset, submitted_by=contributor_user, previous_file_key="a", new_file_key="b",
            diff_percentage=5.0, change_summary={}, submitter_message="fix typo",
            status=DatasetRevision.Status.APPROVED,
        )

        self.client.force_authenticate(contributor_user)
        resp = self.client.get("/api/datasets/dashboard/my-contributions/")
        titles = {d["title"] for d in resp.data}
        self.assertIn("MC DS", titles)

    def test_pending_revision_not_yet_counted(self):
        owner = make_user("mcowner2", "mcowner2@aastu.edu.et", role="researcher")
        contributor_user = make_user("mccontrib2", "mccontrib2@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "MC DS Pending")
        DatasetRevision.objects.create(
            dataset=dataset, submitted_by=contributor_user, previous_file_key="a", new_file_key="b",
            diff_percentage=5.0, change_summary={}, submitter_message="pending change",
            status=DatasetRevision.Status.PENDING,
        )

        self.client.force_authenticate(contributor_user)
        resp = self.client.get("/api/datasets/dashboard/my-contributions/")
        self.assertEqual(len(resp.data), 0)

    def test_own_dataset_not_counted_as_contribution(self):
        owner = make_user("mcowner3", "mcowner3@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "MC Own DS")
        DatasetRevision.objects.create(
            dataset=dataset, submitted_by=owner, previous_file_key="a", new_file_key="b",
            diff_percentage=5.0, change_summary={}, submitter_message="self edit",
            status=DatasetRevision.Status.APPROVED,
        )

        self.client.force_authenticate(owner)
        resp = self.client.get("/api/datasets/dashboard/my-contributions/")
        self.assertEqual(len(resp.data), 0)