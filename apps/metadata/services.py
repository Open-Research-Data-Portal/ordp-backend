import re
import difflib
from django.db.models import F

from apps.datasets.models import Dataset
from .models import Category, FallbackThumbnail


# Hand-maintained: add an entry here whenever you notice an abbreviation
# or synonym causing near-duplicate categories. Left side is lowercase
# and is what a user might type; right side must match an existing
# category's name exactly (case-insensitive) or the mapping is ignored.
CATEGORY_SYNONYMS = {
    "ai": "Artificial Intelligence",
    "ml": "Machine Learning",
    "cs": "Computer Science",
    "it": "Information Technology",
}


def _normalize_for_matching(name):
    """Lowercase, strip punctuation/extra whitespace, and de-pluralize simple
    trailing forms so 'Agricultures', 'agriculture ', and 'AGRICULTURE' all
    collapse to the same comparison key. Not a full stemmer — just enough
    to catch the common cases people actually type."""
    cleaned = re.sub(r"[^\w\s]", "", name).strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if cleaned.endswith("ies") and len(cleaned) > 4:
        cleaned = cleaned[:-3] + "y"
    elif cleaned.endswith("es") and len(cleaned) > 3:
        cleaned = cleaned[:-2]
    elif cleaned.endswith("s") and not cleaned.endswith("ss") and len(cleaned) > 3:
        cleaned = cleaned[:-1]

    return cleaned


def find_matching_category(name, threshold=0.87):
    """Looks for an existing category that's the same thing the user typed.
    Checks in order:
    1. A hand-maintained synonym/abbreviation ('AI' -> 'Artificial
       Intelligence') — always trusted, no fuzziness involved.
    2. Normalized + fuzzy matching against real (standard) categories first,
       then other 'other'-origin categories, so a near-duplicate always
       prefers the admin-created category over another user's earlier typo.
    Returns None if nothing is confident enough, so a new category gets
    created as before."""
    name = name.strip()
    if not name:
        return None

    mapped_name = CATEGORY_SYNONYMS.get(name.lower())
    if mapped_name:
        mapped = Category.objects.filter(name__iexact=mapped_name).first()
        if mapped:
            return mapped

    target = _normalize_for_matching(name)
    if not target:
        return None

    for queryset in (
        Category.objects.filter(origin=Category.Origin.STANDARD),
        Category.objects.exclude(origin=Category.Origin.STANDARD),
    ):
        best_match, best_ratio = None, 0.0
        for category in queryset.only("id", "name"):
            candidate = _normalize_for_matching(category.name)
            if not candidate:
                continue
            if candidate == target:
                return category
            ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
            if ratio > best_ratio:
                best_ratio, best_match = ratio, category
        if best_match and best_ratio >= threshold:
            return best_match

    return None


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
    Before creating a new row, the typed name is checked against a synonym
    list and then normalized/fuzzy-matched against existing categories, so
    'AI', 'Agricultures', and a small typo like 'Agriculure' all reuse the
    same real category instead of spawning near-duplicates."""
    name = name.strip()
    existing = find_matching_category(name)
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