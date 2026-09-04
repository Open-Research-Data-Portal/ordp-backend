# apps/metadata/management/commands/seed_fallback_thumbnails.py
import io
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from apps.datasets.services.storage import upload_fileobj
from apps.metadata.models import Category, FallbackThumbnail


# Override the search term for categories whose name alone won't
# return good Pexels results. Anything not listed here just uses
# its own category name as the search query.
QUERY_OVERRIDES = {
    "ICT": "computer technology",
    "Health Sciences": "medical laboratory",
    # add more overrides as needed
}


class Command(BaseCommand):
    help = (
        "Seeds fallback thumbnails for every standard (admin-created) category "
        "in one run, searching Pexels and uploading results to Backblaze."
    )

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=3)
        parser.add_argument(
            "--only",
            type=str,
            default=None,
            help="Comma-separated category names to limit the run to (optional).",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip categories that already have at least one FallbackThumbnail.",
        )

    def handle(self, *args, **options):
        count = options["count"]
        only = options["only"]
        skip_existing = options["skip_existing"]

        api_key = getattr(settings, "PEXELS_API_KEY", None)
        if not api_key:
            raise CommandError("PEXELS_API_KEY is not set in settings/env.")

        categories = Category.objects.filter(origin=Category.Origin.STANDARD).order_by("name")

        if only:
            names = [n.strip() for n in only.split(",")]
            categories = categories.filter(name__in=names)

        if not categories.exists():
            self.stderr.write("No matching standard categories found.")
            return

        for category in categories:
            if skip_existing and FallbackThumbnail.objects.filter(category=category).exists():
                self.stdout.write(f"Skipping '{category.name}' (already seeded).")
                continue

            query = QUERY_OVERRIDES.get(category.name, category.name)
            self.stdout.write(f"Seeding '{category.name}' (query: '{query}')...")

            try:
                resp = requests.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization": api_key},
                    params={"query": query, "per_page": count},
                    timeout=15,
                )
                resp.raise_for_status()
                photos = resp.json().get("photos", [])
            except requests.RequestException as exc:
                self.stderr.write(f"  Failed to search Pexels for '{category.name}': {exc}")
                continue

            if not photos:
                self.stderr.write(f"  No Pexels results for '{query}'.")
                continue

            for i, photo in enumerate(photos):
                image_url = photo["src"]["medium"]
                try:
                    img_resp = requests.get(image_url, timeout=15)
                    img_resp.raise_for_status()
                except requests.RequestException as exc:
                    self.stderr.write(f"  Failed to download image: {exc}")
                    continue

                object_key = f"fallback-thumbnails/{category.id}/{i}.jpg"
                upload_fileobj(io.BytesIO(img_resp.content), object_key, content_type="image/jpeg")

                FallbackThumbnail.objects.get_or_create(
                    category=category,
                    image_key=object_key,
                )
                self.stdout.write(self.style.SUCCESS(f"  Seeded {object_key}"))