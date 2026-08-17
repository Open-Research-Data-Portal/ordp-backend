from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from .models import Dataset, Contributor


def make_dataset(owner, title="Test DS"):
    return Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.APPROVED)


class ContributorTypeUpdateTests(APITestCase):
    def setUp(self):
        self.owner = make_user("coowner", "coowner@aastu.edu.et")
        self.contributor_user = make_user("cocontrib", "cocontrib@aastu.edu.et", role="researcher")
        self.dataset = make_dataset(self.owner)
        self.contributor = Contributor.objects.create(
            dataset=self.dataset, user=self.contributor_user, name="Contrib",
            contributor_type=Contributor.ContributorType.CONTRIBUTOR,
        )

    def test_owner_can_promote_contributor_to_owner(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/datasets/{self.dataset.id}/contributors/{self.contributor.id}/",
            {"contributor_type": "owner"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.contributor.refresh_from_db()
        self.assertEqual(self.contributor.contributor_type, Contributor.ContributorType.OWNER)

    def test_promoted_coowner_passes_is_owned_by(self):
        self.contributor.contributor_type = Contributor.ContributorType.OWNER
        self.contributor.save(update_fields=["contributor_type"])
        self.assertTrue(self.dataset.is_owned_by(self.contributor_user))

    def test_non_owner_contributor_cannot_promote_anyone(self):
        """Co-owners can't mint more co-owners — only the original owner can."""
        self.contributor.contributor_type = Contributor.ContributorType.OWNER
        self.contributor.save(update_fields=["contributor_type"])
        another_contributor_user = make_user("coother", "coother@aastu.edu.et", role="researcher")
        another_contributor = Contributor.objects.create(
            dataset=self.dataset, user=another_contributor_user, name="Other",
            contributor_type=Contributor.ContributorType.CONTRIBUTOR,
        )

        self.client.force_authenticate(self.contributor_user)  
        resp = self.client.patch(
            f"/api/datasets/{self.dataset.id}/contributors/{another_contributor.id}/",
            {"contributor_type": "owner"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_plain_researcher_cannot_promote(self):
        outsider = make_user("cooutsider", "cooutsider@aastu.edu.et", role="researcher")
        self.client.force_authenticate(outsider)
        resp = self.client.patch(
            f"/api/datasets/{self.dataset.id}/contributors/{self.contributor.id}/",
            {"contributor_type": "owner"},
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_contributor_type_rejected(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.patch(
            f"/api/datasets/{self.dataset.id}/contributors/{self.contributor.id}/",
            {"contributor_type": "supreme_leader"},
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class CoOwnerAccessTests(APITestCase):
    def setUp(self):
        self.owner = make_user("caowner", "caowner@aastu.edu.et")
        self.coowner_user = make_user("cacoowner", "cacoowner@aastu.edu.et", role="researcher")
        self.dataset = make_dataset(self.owner)
        Contributor.objects.create(
            dataset=self.dataset, user=self.coowner_user, name="CoOwner",
            contributor_type=Contributor.ContributorType.OWNER,
        )

    def test_coowner_can_soft_delete(self):
        self.client.force_authenticate(self.coowner_user)
        resp = self.client.delete(f"/api/datasets/{self.dataset.id}/delete/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

    def test_plain_contributor_cannot_soft_delete(self):
        plain_contributor_user = make_user("caplain", "caplain@aastu.edu.et", role="researcher")
        Contributor.objects.create(
            dataset=self.dataset, user=plain_contributor_user, name="Plain",
            contributor_type=Contributor.ContributorType.CONTRIBUTOR,
        )
        self.client.force_authenticate(plain_contributor_user)
        resp = self.client.delete(f"/api/datasets/{self.dataset.id}/delete/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)