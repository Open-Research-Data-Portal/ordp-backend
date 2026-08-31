from django.utils import timezone

from apps.datasets.models import Dataset


def is_under_embargo(dataset):
    """
    Return True when a public dataset has an active embargo.

    An embargo only applies to public datasets. A missing embargo
    date means there is no active embargo.
    """
    return (
        dataset.visibility == Dataset.Visibility.PUBLIC
        and dataset.embargo_end_date is not None
        and timezone.now() < dataset.embargo_end_date
    )