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


def get_or_create_pending_category(name, user):
    """If a matching category already exists (case-insensitive), reuse it instead
    of creating a duplicate — avoids 'Agriculture' and 'agriculture' both existing
    as separate pending entries from two different people."""
    name = name.strip()
    existing = Category.objects.filter(name__iexact=name).first()
    if existing:
        return existing
    return Category.objects.create(name=name, status=Category.Status.PENDING, suggested_by=user)