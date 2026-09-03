from django.db.models import F

from apps.datasets.models import Dataset
from .models import Category, FallbackThumbnail


def assign_fallback_thumbnail(dataset):
    """Called right after category is known and only if the uploader didn't provide
    their own thumbnail. Picks whichever fallback in this category has been used
    least, so the same image doesn't cluster on too many datasets."""
    if dataset.thumbnail_key:
        return
    fallback = FallbackThumbnail.objects.filter(category=dataset.metadata.category).order_by("usage_count").first()
    if fallback is None:
        return
    dataset.thumbnail_key = fallback.image_key
    dataset.thumbnail_source = Dataset.ThumbnailSource.FALLBACK_AUTO
    dataset.save(update_fields=["thumbnail_key", "thumbnail_source"])
    FallbackThumbnail.objects.filter(id=fallback.id).update(usage_count=F("usage_count") + 1)


def get_or_create_category(name, user, origin):
    """No approval step: an 'other' category is usable and saved immediately.
    Case-insensitive match so 'Agriculture' and 'agriculture' don't become two
    separate rows, and so an interest-other entry reuses a category that already
    exists from a dataset-other entry (or vice versa)."""
    name = name.strip()
    existing = Category.objects.filter(name__iexact=name).first()
    if existing:
        return existing
    return Category.objects.create(name=name, origin=origin, suggested_by=user)


def get_or_create_category_from_dataset_other(name, user):
    """'Other' category typed while uploading a dataset. Gets added to the
    general category list for future dataset category selection."""
    return get_or_create_category(name, user, Category.Origin.DATASET_OTHER)


def get_or_create_category_from_interest_other(name, user):
    """'Other' interest typed at onboarding/profile. Saved for the user only —
    never shown as a selectable category or interest option to anyone else."""
    return get_or_create_category(name, user, Category.Origin.INTEREST_OTHER)


def get_or_create_pending(model, name, user):
    """Still used by Language and DatasetCharacteristic, which keep their
    admin-review workflow — untouched."""
    name = name.strip()
    existing = model.objects.filter(name__iexact=name).first()
    if existing:
        return existing
    return model.objects.create(name=name, status=model.Status.PENDING, suggested_by=user)


def get_or_create_pending_language(name, user):
    from .models import Language
    return get_or_create_pending(Language, name, user)