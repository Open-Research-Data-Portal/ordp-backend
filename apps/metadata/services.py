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


def get_or_create_pending(model, name, user):
    """Shared by Category, Language, and DatasetCharacteristic — same 'usable
    immediately, invisible until approved' pattern for any admin-reviewed
    taxonomy. Case-insensitive match avoids 'Agriculture' and 'agriculture'
    both existing as separate pending entries from two different people."""
    name = name.strip()
    existing = model.objects.filter(name__iexact=name).first()
    if existing:
        return existing
    return model.objects.create(name=name, status=model.Status.PENDING, suggested_by=user)


def get_or_create_pending_category(name, user):
    return get_or_create_pending(Category, name, user)


def get_or_create_pending_language(name, user):
    from .models import Language
    return get_or_create_pending(Language, name, user)