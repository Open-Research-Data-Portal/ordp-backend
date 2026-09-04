import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.datasets.services.storage import upload_fileobj
from apps.metadata.models import Category, FallbackThumbnail


QUERY_OVERRIDES = {
    "ICT": "computer technology",
    "Health Sciences": "medical laboratory",
}


class Command(BaseCommand):
    help = (
        "Seeds fallback thumbnails for every standard (admin-created) category "
        "using Pexels and uploads the images to Backblaze."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=3,
            help="Number of thumbnails to seed per category.",
        )

        parser.add_argument(
            "--only",
            type=str,
            default=None,
            help="Comma-separated category names to limit the run to.",
        )

        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip categories that already have thumbnails.",
        )

    def handle(self, *args, **options):
        count = options["count"]
        only = options["only"]
        skip_existing = options["skip_existing"]

        if count < 1:
            raise CommandError("--count must be at least 1.")

        api_key = getattr(settings, "PEXELS_API_KEY", None)

        if not api_key:
            raise CommandError(
                "PEXELS_API_KEY is not set in settings/env."
            )

        categories = (
            Category.objects
            .filter(origin=Category.Origin.STANDARD)
            .order_by("name")
        )

        if only:
            names = [name.strip() for name in only.split(",") if name.strip()]
            categories = categories.filter(name__in=names)

        if not categories.exists():
            self.stderr.write(
                self.style.WARNING(
                    "No matching standard categories found."
                )
            )
            return

        for category in categories:

            if (
                skip_existing
                and FallbackThumbnail.objects.filter(
                    category=category
                ).exists()
            ):
                self.stdout.write(
                    f"Skipping '{category.name}' (already seeded)."
                )
                continue

            query = QUERY_OVERRIDES.get(
                category.name,
                category.name,
            )

            self.stdout.write(
                f"Seeding '{category.name}' (query: '{query}')..."
            )

            # ---------------------------------
            # Search Pexels
            # ---------------------------------
            params = urlencode(
                {
                    "query": query,
                    "per_page": count,
                }
            )

            search_url = (
                f"https://api.pexels.com/v1/search?{params}"
            )

            request = Request(
                search_url,
                headers={
                    "Authorization": api_key,
                    "User-Agent": "ORDP/1.0",
                },
            )

            try:
                with urlopen(request, timeout=15) as response:
                    data = json.loads(
                        response.read().decode("utf-8")
                    )

                photos = data.get("photos", [])

            except HTTPError as exc:
                self.stderr.write(
                    f"  Failed to search Pexels for "
                    f"'{category.name}': HTTP {exc.code}"
                )
                continue

            except (URLError, TimeoutError) as exc:
                self.stderr.write(
                    f"  Failed to search Pexels for "
                    f"'{category.name}': {exc}"
                )
                continue

            if not photos:
                self.stderr.write(
                    f"  No Pexels results for '{query}'."
                )
                continue

            # ---------------------------------
            # Download and store images
            # ---------------------------------
            for i, photo in enumerate(photos):

                image_url = photo.get("src", {}).get("medium")

                if not image_url:
                    self.stderr.write(
                        f"  Skipping photo {i}: no medium image URL."
                    )
                    continue

                try:
                    image_request = Request(
                        image_url,
                        headers={
                            "User-Agent": "ORDP/1.0",
                        },
                    )

                    with urlopen(
                        image_request,
                        timeout=15,
                    ) as response:
                        image_data = response.read()

                except HTTPError as exc:
                    self.stderr.write(
                        f"  Failed to download image {i}: "
                        f"HTTP {exc.code}"
                    )
                    continue

                except (URLError, TimeoutError) as exc:
                    self.stderr.write(
                        f"  Failed to download image {i}: {exc}"
                    )
                    continue

                # ---------------------------------
                # Backblaze B2 object key
                # ---------------------------------
                object_key = (
                    f"fallback-thumbnails/{category.id}/{i}.jpg"
                )

                try:
                    upload_fileobj(
                        io.BytesIO(image_data),
                        object_key,
                        content_type="image/jpeg",
                    )

                    FallbackThumbnail.objects.get_or_create(
                        category=category,
                        image_key=object_key,
                    )

                except Exception as exc:
                    self.stderr.write(
                        f"  Failed to upload {object_key}: {exc}"
                    )
                    continue

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Seeded {object_key}"
                    )
                )