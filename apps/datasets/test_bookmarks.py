from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from .models import Dataset, Bookmark


def make_dataset(owner, title="Test DS"):
    return Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.APPROVED)


class ToggleBookmarkTests(APITestCase):
    def test_bookmark_then_unbookmark(self):
        user = make_user("bmuser", "bmuser@aastu.edu.et")
        owner = make_user("bmowner", "bmowner@aastu.edu.et")
        dataset = make_dataset(owner)

        self.client.force_authenticate(user)
        resp = self.client.post(f"/api/datasets/{dataset.id}/bookmark/")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["bookmarked"])
        self.assertTrue(Bookmark.objects.filter(user=user, dataset=dataset).exists())

        resp = self.client.post(f"/api/datasets/{dataset.id}/bookmark/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data["bookmarked"])
        self.assertFalse(Bookmark.objects.filter(user=user, dataset=dataset).exists())

    def test_unauthenticated_cannot_bookmark(self):
        owner = make_user("bmowner2", "bmowner2@aastu.edu.et")
        dataset = make_dataset(owner)
        resp = self.client.post(f"/api/datasets/{dataset.id}/bookmark/")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_bookmarking_inactive_dataset_404s(self):
        user = make_user("bmuser2", "bmuser2@aastu.edu.et")
        owner = make_user("bmowner3", "bmowner3@aastu.edu.et")
        dataset = make_dataset(owner)
        dataset.is_active = False
        dataset.save(update_fields=["is_active"])

        self.client.force_authenticate(user)
        resp = self.client.post(f"/api/datasets/{dataset.id}/bookmark/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class MyBookmarksTests(APITestCase):
    def test_lists_only_my_bookmarks(self):
        user = make_user("mbuser", "mbuser@aastu.edu.et")
        other_user = make_user("mbother", "mbother@aastu.edu.et")
        owner = make_user("mbowner", "mbowner@aastu.edu.et")
        dataset1 = make_dataset(owner, "DS One")
        dataset2 = make_dataset(owner, "DS Two")

        Bookmark.objects.create(user=user, dataset=dataset1)
        Bookmark.objects.create(user=other_user, dataset=dataset2)

        self.client.force_authenticate(user)
        resp = self.client.get("/api/datasets/bookmarks/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        returned_ids = {str(d["id"]) for d in resp.data}
        self.assertEqual(returned_ids, {str(dataset1.id)})

    def test_excludes_inactive_bookmarked_dataset(self):
        user = make_user("mbuser2", "mbuser2@aastu.edu.et")
        owner = make_user("mbowner2", "mbowner2@aastu.edu.et")
        dataset = make_dataset(owner, "Inactive DS")
        Bookmark.objects.create(user=user, dataset=dataset)
        dataset.is_active = False
        dataset.save(update_fields=["is_active"])

        self.client.force_authenticate(user)
        resp = self.client.get("/api/datasets/bookmarks/")
        self.assertEqual(len(resp.data), 0)