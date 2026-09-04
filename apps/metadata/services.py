import re
import unicodedata
from difflib import SequenceMatcher
from django.db.models import F

from apps.datasets.models import Dataset
from .models import Category, FallbackThumbnail


# Hand-maintained: add an entry whenever a real abbreviation/synonym should
# merge into an existing category instead of becoming its own row.
# Left side lowercase (what a user might type), right side must match an
# existing category's name exactly (case-insensitive) or is ignored.
CATEGORY_SYNONYMS = {
    "ai": "Artificial Intelligence",
    "ml": "Machine Learning",
    "cs": "Computer Science",
    "it": "Information Technology",
    "nlp": "Natural Language Processing",
    "gis": "Geographic Information Systems",
}

# Acronyms allowed to render in uppercase during display normalization.
# Anything NOT in this set is title-cased normally, no matter how the
# user typed it — this is what fixes "DATA" / "WEB" being misread as
# acronyms just because they're short or the user had caps lock on.
KNOWN_ACRONYMS = {"AI", "ML", "CS", "IT", "NLP", "GIS", "CNN", "MRI", "GPS", "API", "SQL", "IOT"}


def normalize_taxonomy_name(name):
    """
    Normalize a user-entered taxonomy/category name for consistent display.

        "  Machine   Learning  " -> "Machine Learning"
        "machine-learning"       -> "Machine Learning"
        "MACHINE LEARNING"       -> "Machine Learning"   (caps lock ≠ acronym)
        "ai"                     -> "AI"                 (whitelisted acronym)
        "data"                   -> "Data"                (NOT treated as an acronym)

    Only acronyms in KNOWN_ACRONYMS render uppercase; everything else is
    title-cased regardless of how it was typed.
    """
    if not name:
        return ""

    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[_-]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"\s+([,./&()])", r"\1", name)
    name = re.sub(r"([,./&()])\s+", r"\1 ", name)
    name = name.strip()

    if not name:
        return ""

    normalized_words = []
    for word in name.split():
        alpha_numeric = re.sub(r"[^A-Za-z0-9]", "", word)

        if alpha_numeric.upper() in KNOWN_ACRONYMS:
            normalized_words.append(alpha_numeric.upper())
            continue

        normalized_words.append(word[:1].upper() + word[1:].lower())

    return " ".join(normalized_words)


def _comparison_name(name):
    """Spaces/punctuation-insensitive representation for similarity checks."""
    normalized = normalize_taxonomy_name(name)
    return re.sub(r"[^a-z0-9]", "", normalized.lower())


def _singularize_word(word):
    """Lightweight singularization for comparison only — never changes the
    stored name."""
    word = word.lower()
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ves") and len(word) > 4:
        return word[:-3] + "f"
    if word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _comparison_name_singular(name):
    normalized = normalize_taxonomy_name(name)
    singular_words = [_singularize_word(w) for w in normalized.split()]
    return re.sub(r"[^a-z0-9]", "", "".join(singular_words).lower())


def _category_similarity(name1, name2):
    a, b = _comparison_name(name1), _comparison_name(name2)
    a_s, b_s = _comparison_name_singular(name1), _comparison_name_singular(name2)
    if not a or not b:
        return 0.0
    return max(
        SequenceMatcher(None, a, b).ratio(),
        SequenceMatcher(None, a_s, b_s).ratio(),
    )


def find_similar_category(name):
    """
    Finds an existing category equivalent to the supplied name.

    Priority:
        1. A known synonym/abbreviation mapping (admin-curated).
        2. Exact match after normalization.
        3. High-confidence fuzzy match — standard categories checked
           before other 'other'-origin categories, so a near-duplicate
           always prefers the real admin category.
        4. No match -> None (a new category gets created).
    """
    if not name or not name.strip():
        return None

    synonym_target = CATEGORY_SYNONYMS.get(name.strip().lower())
    if synonym_target:
        mapped = Category.objects.filter(name__iexact=synonym_target).first()
        if mapped:
            return mapped

    normalized_name = normalize_taxonomy_name(name)
    if not normalized_name:
        return None

    for queryset in (
        Category.objects.filter(origin=Category.Origin.STANDARD),
        Category.objects.exclude(origin=Category.Origin.STANDARD),
    ):
        exact = queryset.filter(name__iexact=normalized_name).first()
        if exact:
            return exact

        best_match, best_score = None, 0.0
        for category in queryset.only("id", "name"):
            score = _category_similarity(normalized_name, category.name)
            if score > best_score:
                best_score, best_match = score, category
        if best_match and best_score >= 0.90:
            return best_match

    return None
def find_similar_term(model, name):
    """
    Generic version of find_similar_category for taxonomy-style models that
    have `name` and `status` fields (Language, DatasetCharacteristic). Only
    matches against already-APPROVED rows, so a new entry reuses a real,
    established term instead of creating a near-duplicate.
    """
    normalized_name = normalize_taxonomy_name(name)
    if not normalized_name:
        return None

    approved = model.objects.filter(status=model.Status.APPROVED)

    exact = approved.filter(name__iexact=normalized_name).first()
    if exact:
        return exact

    best_match, best_score = None, 0.0
    for term in approved.only("id", "name"):
        score = _category_similarity(normalized_name, term.name)
        if score > best_score:
            best_score, best_match = score, term

    if best_match and best_score >= 0.90:
        return best_match

    return None


def get_or_create_approved_term(model, name, user):
    """
    Used by the Language and DatasetCharacteristic 'other' flows. Unlike
    get_or_create_pending, this saves the new entry as APPROVED immediately
    — no admin review step — so it's usable on the current dataset right
    away and shows up in the picker for every future upload. Normalization
    and fuzzy matching keep near-duplicates from piling up without a human
    needing to catch them first.
    """
    name = (name or "").strip()
    if not name:
        return None

    existing = find_similar_term(model, name)
    if existing:
        return existing

    return model.objects.create(
        name=normalize_taxonomy_name(name),
        status=model.Status.APPROVED,
        suggested_by=user,
    )

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
    The typed name is checked against synonyms, then normalized/fuzzy-matched
    against existing categories; if nothing matches, a new category is
    created using the normalized (consistently cased) name."""
    name = name.strip()
    existing = find_similar_category(name)
    if existing:
        return existing
    return Category.objects.create(
        name=normalize_taxonomy_name(name),
        origin=origin,
        suggested_by=user,
    )


def get_or_create_category_from_dataset_other(name, user):
    return get_or_create_category(name, user, Category.Origin.DATASET_OTHER)


def get_or_create_category_from_interest_other(name, user):
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