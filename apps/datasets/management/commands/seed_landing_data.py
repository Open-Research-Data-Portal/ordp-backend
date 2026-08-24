"""
Seed demo data for the ORDP frontend build-out: landing page, browse/Datasets
page, Researcher Dashboard, My Datasets, and Bookmarks.

Usage:
    python manage.py seed_landing_data
    python manage.py seed_landing_data --flush   # wipe previously-seeded rows first

Place this file at:
    <your_datasets_app>/management/commands/seed_landing_data.py

You MUST fix these before running, if your project layout differs:
  1. Import paths below use `apps.accounts`, `apps.metadata`, and
     `apps.datasets` (confirmed against your INSTALLED_APPS structure).
     If any of these app labels differ, update the corresponding import.
  2. `get_user_model()` is used generically. If your custom User model has
     required fields beyond email/username/password, add them to
     `create_user(...)` below.
  3. `thumbnail_key` is a plain CharField, not a real file. This script
     seeds it with placeholder path-like strings (e.g.
     "thumbnails/agriculture/1.jpg"). Whatever code turns `thumbnail_key`
     into a servable URL (a storage backend, a signed-URL helper, a
     custom serializer method) needs matching objects to actually exist —
     otherwise the frontend will get 404s on image src. If you don't have
     that wired up yet, swap PLACEHOLDER_IMAGE_URLS in directly as the
     thumbnail_key value so the frontend can render something meaningful
     immediately (done below, `USE_FULL_URLS_AS_KEYS = True`).

CHANGES IN THIS REVISION (vs. previous):
  - SWITCHED THUMBNAIL SOURCE from Unsplash's "Source" endpoint
    (source.unsplash.com) to picsum.photos. Unsplash Source has been
    discontinued and no longer resolves to real images — every seeded
    thumbnail_key was pointing at a dead URL, which is why cards showed
    the "no image" placeholder even after the serializer correctly
    exposed thumbnail_key. picsum.photos was confirmed live (backed by
    Fastly CDN, no API key required) and supports a stable "/seed/{seed}"
    path that returns the same photo every time for a given seed string —
    see https://picsum.photos for docs. This trades topical relevance
    (Unsplash search terms loosely matched dataset subject matter; picsum
    seeds just return a consistent-but-arbitrary photo) for actually
    working images. THUMBNAIL_TOPICS is kept below only as a comment for
    reference / easy reversal if you later wire up the real Unsplash API
    (api.unsplash.com, which requires a registered API key) instead of
    the deprecated Source shortcut.
  - Two datasets are seeded with NO thumbnail at all (thumbnail_key=""),
    so frontend "no thumbnail" placeholder states are actually exercised
    instead of always having an image to fall back on.
    (thumbnail_key is CharField(blank=True) WITHOUT null=True, so the DB
    column is NOT NULL — the correct "empty" value is "", not None.)
  - Three datasets now get multiple DatasetFile rows (2-4 files) instead
    of exactly one, so file-count badges in the UI show real variation.
  - sosena.gossaye (the demo/test account) now owns 8 datasets instead of
    2, so pagination controls on "My Datasets" actually have something to
    paginate.
  - One dataset is forced to created_at = now (days_ago = 0) so "Updated
    today" text is reachable, not just "Updated N days ago".
  - One dataset is seeded with an empty keywords list, to confirm that
    path serializes cleanly.
  - Contributors: 3 published datasets now get an additional CONTRIBUTOR
    (not just OWNER), so multi-contributor UI has real data to render.

NOTE: picsum.photos is explicitly a placeholder/demo service (per its own
docs) — fine for seed data now, but not something to depend on for real
production dataset thumbnails. When real thumbnails are wired up (actual
uploaded files or a storage-backed URL resolver), this seed data should
be regenerated against that instead.
"""

import random
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    College,
    CenterOfExcellence,
    Department,
    ResearchCategory,
    UserProfile,
    UserRole,
    ActivityLog,
)
from apps.metadata.models import (
    Category,
    Subject,
    Keyword,
    Language,
    DatasetCharacteristic,
    Metadata,
    FallbackThumbnail,
)
from apps.datasets.models import (
    Dataset,
    DatasetFile,
    Contributor,
    Bookmark,
    DatasetVersion,
)

User = get_user_model()

USE_FULL_URLS_AS_KEYS = True

# Kept for reference only (not used while picsum.photos is the thumbnail
# source below). If you later switch to the real Unsplash API
# (api.unsplash.com, requires an API key) for topically-relevant photos,
# these search terms are a ready-made mapping to reuse.
THUMBNAIL_TOPICS = {
    "agri-1": "agriculture,farm,crops",
    "nlp-1": "text,language,books",
    "traffic-1": "traffic,city,road",
    "urban-1": "city,aerial,urban",
    "speech-1": "microphone,audio,recording",
    "biodiversity-1": "wildlife,forest,nature",
    "soil-1": "soil,earth,field",
    "air-quality-1": "smog,sky,pollution",
    "census-1": "crowd,people,city",
    "climate-1": "climate,weather,earth",
    "crispr-1": "laboratory,science,dna",
    "mobility-1": "bus,transit,commute",
}


def thumb_for(seed):
    # thumbnail_key is CharField(blank=True) WITHOUT null=True, so the DB
    # column is NOT NULL — the correct "empty" value is "", not None.
    if seed is None:
        return ""
    if USE_FULL_URLS_AS_KEYS:
        # picsum.photos/seed/{seed}/{width}/{height} returns the SAME photo
        # every time for a given seed string — confirmed live, no API key
        # needed. Not topically matched to the dataset subject (unlike the
        # old Unsplash Source approach), but it actually resolves to a
        # real image, which Unsplash Source no longer does.
        return f"https://picsum.photos/seed/{seed}/600/400"
    return f"thumbnails/{seed}.jpg"


class Command(BaseCommand):
    help = "Seed demo data for the landing page, Datasets browse page, Researcher Dashboard, My Datasets, and Bookmarks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete previously-seeded demo rows (matched by email domain @demo.ordp) before reseeding.",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        with transaction.atomic():
            colleges, coes, departments = self._seed_org_structure()
            research_categories = self._seed_research_categories()
            categories, subjects, keywords, languages, characteristics = self._seed_taxonomy()
            self._seed_fallback_thumbnails(categories)
            users = self._seed_users(departments, research_categories)
            datasets = self._seed_datasets(users, categories, subjects, keywords, languages)
            self._seed_bookmarks(users, datasets)
            self._seed_activity_log(users, datasets)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(users)} users and {len(datasets)} datasets."
        ))
        self.stdout.write(self.style.WARNING(
            "Demo user password for all seeded accounts: DemoPass123!"
        ))

    def _flush(self):
        demo_users = User.objects.filter(email__endswith="@demo.ordp")
        Dataset.objects.filter(owner__in=demo_users).delete()
        demo_users.delete()
        self.stdout.write(self.style.WARNING("Flushed previously-seeded demo users and their datasets."))

    def _seed_org_structure(self):
        college_names = [
            "College of Electrical & Mechanical Engineering",
            "College of Applied Sciences",
            "College of Architecture & Civil Engineering",
        ]
        colleges = [College.objects.get_or_create(name=n)[0] for n in college_names]

        coe_names = ["Center of Excellence in AI & Robotics", "Center of Excellence in Biotechnology"]
        coes = [CenterOfExcellence.objects.get_or_create(name=n)[0] for n in coe_names]

        dept_specs = [
            ("Computer Science & Engineering", colleges[0], None),
            ("Electrical Engineering", colleges[0], None),
            ("Environmental Science", colleges[1], None),
            ("Biology", colleges[1], None),
            ("Civil Engineering", colleges[2], None),
            ("Urban Planning", colleges[2], None),
            ("AI & Robotics Research", None, coes[0]),
            ("Applied Biotechnology", None, coes[1]),
        ]
        departments = []
        for name, college, coe in dept_specs:
            dept, _ = Department.objects.get_or_create(
                name=name,
                college=college,
                center_of_excellence=coe,
            )
            departments.append(dept)
        return colleges, coes, departments

    def _seed_research_categories(self):
        names = [
            "Machine Learning", "Natural Language Processing", "Civil Engineering",
            "Urban Planning", "Biology", "Environmental Science", "Agriculture",
            "Public Health", "Statistics",
        ]
        return [
            ResearchCategory.objects.get_or_create(name=n, defaults={"status": ResearchCategory.Status.APPROVED})[0]
            for n in names
        ]

    def _seed_taxonomy(self):
        category_names = [
            "Computer Science", "Education", "Classification", "Computer Vision",
            "NLP", "Data Visualization", "Pre-Trained Model", "Agriculture",
            "Civil Engineering", "Biology", "Environment", "Statistics",
        ]
        categories = {
            n: Category.objects.get_or_create(name=n, defaults={"status": Category.Status.APPROVED})[0]
            for n in category_names
        }

        subject_names = [
            "Crop Yield", "Sentiment Analysis", "Traffic Sensing", "Urban Growth",
            "Speech Recognition", "Biodiversity Survey", "Soil Science", "Air Quality",
            "Census Demographics", "Climate Modeling", "Genomics", "Transit Behavior",
        ]
        subjects = {n: Subject.objects.get_or_create(name=n)[0] for n in subject_names}

        keyword_words = [
            "ethiopia", "amharic", "sensor-data", "geospatial", "audio-corpus",
            "east-africa", "time-series", "survey-data", "climate", "genomics",
            "remote-sensing", "tabular",
        ]
        keywords = {w: Keyword.objects.get_or_create(word=w)[0] for w in keyword_words}

        language_names = ["English", "Amharic", "Oromo", "Tigrinya"]
        languages = {
            n: Language.objects.get_or_create(name=n, defaults={"status": Language.Status.APPROVED})[0]
            for n in language_names
        }

        characteristic_names = ["Well-documented", "Well-maintained", "Clean data", "Original", "High-quality notebooks"]
        characteristics = {
            n: DatasetCharacteristic.objects.get_or_create(name=n, defaults={"status": DatasetCharacteristic.Status.APPROVED})[0]
            for n in characteristic_names
        }

        return categories, subjects, keywords, languages, characteristics

    def _seed_fallback_thumbnails(self, categories):
        for i, (name, category) in enumerate(categories.items()):
            FallbackThumbnail.objects.get_or_create(
                category=category,
                image_key=thumb_for(f"fallback-{i}"),
            )

    def _seed_users(self, departments, research_categories):
        dept_by_name = {d.name: d for d in departments}

        specs = [
            ("sarah.jenkins", "Dr. Sarah Jenkins", "researcher", "Environmental Science", "dr", True),
            ("michael.chen", "Michael Chen", "data_scientist", "AI & Robotics Research", "mr", True),
            ("abebe.bekele", "Dr. Abebe Bekele", "professor", "Environmental Science", "dr", True),
            ("nlp.lab", "AASTU NLP Lab", "researcher", "Computer Science & Engineering", "none", True),
            ("civil.dept", "Civil Engineering Dept", "lecturer", "Civil Engineering", "eng", True),
            ("urban.dept", "Urban Planning Dept", "researcher", "Urban Planning", "mr", True),
            ("biology.group", "Biology Research Group", "researcher", "Biology", "dr", True),
            ("stats.dept", "Statistics Department", "lecturer", "Civil Engineering", "ms", True),
            ("sosena.gossaye", "Sosena Gossaye", "student", None, "none", False),
        ]

        users = []
        for username, full_name, academia, dept_name, title, complete in specs:
            email = f"{username}@demo.ordp"
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": email},
            )
            if created:
                user.set_password("DemoPass123!")
                user.email = email
                user.save()

            department = dept_by_name.get(dept_name) if dept_name else None

            profile, _ = UserProfile.objects.update_or_create(
                user=user,
                defaults=dict(
                    full_name=full_name,
                    academia=academia,
                    academic_title=title,
                    department=department,
                    highest_degree="phd" if title == "dr" else "master",
                    bio=f"{full_name} — demo seed profile for {academia}.",
                    profile_visibility="public",
                    terms_accepted=complete,
                    terms_accepted_at=timezone.now() if complete else None,
                    role=UserProfile.Role.RESEARCHER,
                    email_verified=True,
                    research_interests_completed=complete,
                ),
            )
            if complete:
                profile.research_interests.add(*random.sample(research_categories, k=3))
            UserRole.objects.get_or_create(profile=profile, role=UserRole.RoleChoice.RESEARCHER)

            users.append(user)

        return users

    def _seed_datasets(self, users, categories, subjects, keywords, languages):
        by_username = {u.username: u for u in users}

        dataset_specs = [
            dict(
                title="Ethiopian Agricultural Crop Yield 2010-2023",
                owner="abebe.bekele", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Agriculture", subject="Crop Yield",
                keywords=["ethiopia", "time-series", "tabular"], languages=["English"],
                description="Comprehensive agricultural statistics and yield metrics across Ethiopian regional states, 2010-2023.",
                file_type="CSV", file_size=4_200_000, thumb_seed="agri-1",
                views=2400, downloads=2143, extra_files=2, extra_contributor="sosena.gossaye",
            ),
            dict(
                title="Amharic Sentiment Analysis Corpus V2",
                owner="nlp.lab", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="NLP", subject="Sentiment Analysis",
                keywords=["amharic", "east-africa"], languages=["Amharic", "English"],
                description="Annotated text corpora for sentiment classification in Amharic-language media.",
                file_type="JSON", file_size=12_800_000, thumb_seed="nlp-1",
                views=5100, downloads=1892,
            ),
            dict(
                title="Addis Ababa Traffic Flow Sensor Data",
                owner="civil.dept", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.INSTITUTIONAL,
                category="Civil Engineering", subject="Traffic Sensing",
                keywords=["sensor-data", "geospatial"], languages=["English"],
                description="Real-time traffic density, vehicle classification, and congestion indices across Addis Ababa.",
                file_type="CSV", file_size=450_000_000, thumb_seed="traffic-1",
                views=890, downloads=856, extra_files=3,
            ),
            dict(
                title="Ethiopian Urban Growth Patterns",
                owner="urban.dept", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Civil Engineering", subject="Urban Growth",
                keywords=["geospatial", "remote-sensing"], languages=["English"],
                description="Satellite-derived urban expansion indices and settlement tracking across major Ethiopian cities.",
                file_type="GeoJSON", file_size=1_200_000_000, thumb_seed="urban-1",
                views=14100, downloads=3450, extra_contributor="sarah.jenkins",
            ),
            dict(
                title="Amharic Speech Recognition Corpus",
                owner="nlp.lab", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="NLP", subject="Speech Recognition",
                keywords=["amharic", "audio-corpus"], languages=["Amharic"],
                description="Multi-speaker, high-fidelity audio recordings with manual transcriptions for ASR training.",
                file_type="WAV", file_size=15_400_000_000, thumb_seed="speech-1",
                views=11900, downloads=5120, extra_files=4,
            ),
            dict(
                title="Rift Valley Biodiversity Data",
                owner="biology.group", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Biology", subject="Biodiversity Survey",
                keywords=["east-africa", "remote-sensing"], languages=["English"],
                description="Flora and fauna survey inventories across the East African Rift system.",
                file_type="CSV", file_size=15_600_000, thumb_seed="biodiversity-1",
                views=4700, downloads=1205,
            ),
            dict(
                title="Soil Salinity Mapping - Afar Region",
                owner="abebe.bekele", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Environment", subject="Soil Science",
                keywords=["geospatial", "climate"], languages=["English"],
                description="Spatial distribution of soil salinity and electrical conductivity across the Afar region.",
                file_type="TIFF", file_size=320_000_000, thumb_seed=None,
                views=1800, downloads=412,
            ),
            dict(
                title="Addis Ababa Air Quality Index",
                owner="sarah.jenkins", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Environment", subject="Air Quality",
                keywords=["climate", "time-series"], languages=["English"],
                description="Hourly particulate matter (PM2.5, PM10) and gaseous pollutant records for Addis Ababa.",
                file_type="JSON", file_size=8_400_000, thumb_seed="air-quality-1",
                views=3200, downloads=928, force_created_today=True,
            ),
            dict(
                title="National Census Sample 2024",
                owner="stats.dept", status=Dataset.Status.PENDING, visibility=Dataset.Visibility.RESTRICTED,
                category="Statistics", subject="Census Demographics",
                keywords=["survey-data", "tabular"], languages=["English", "Amharic"],
                description="Anonymized household demographic distributions and economic indicators, 2024 sample.",
                file_type="CSV", file_size=85_000_000, thumb_seed="census-1",
                views=2310, downloads=0,
            ),
            dict(
                title="Global Temperature Anomalies 1990-2023",
                owner="sarah.jenkins", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Environment", subject="Climate Modeling",
                keywords=["climate", "time-series"], languages=["English"],
                description="Monthly global surface temperature deviation records, normalized against the 1951-1980 baseline.",
                file_type="CSV", file_size=1_200_000, thumb_seed="climate-1",
                views=6400, downloads=1900,
            ),
            dict(
                title="CRISPR Target Mapping Index",
                owner="michael.chen", status=Dataset.Status.DRAFT, visibility=Dataset.Visibility.RESTRICTED,
                category="Biology", subject="Genomics",
                keywords=["genomics"], languages=["English"],
                description="An in-progress index of candidate CRISPR guide RNA target sites, pending validation.",
                file_type="JSON", file_size=45_800_000, thumb_seed="crispr-1",
                views=0, downloads=0,
            ),
            dict(
                title="Urban Mobility Survey 2024 (Raw)",
                owner="urban.dept", status=Dataset.Status.PENDING, visibility=Dataset.Visibility.INSTITUTIONAL,
                category="Civil Engineering", subject="Transit Behavior",
                keywords=["survey-data"], languages=["English"],
                description="Raw, unprocessed survey responses on commuting patterns and transit usage across metro districts.",
                file_type="ZIP", file_size=80_200_000, thumb_seed="mobility-1",
                views=0, downloads=0,
            ),
            dict(
                title="Teff Yield Prediction Variables",
                owner="sosena.gossaye", status=Dataset.Status.DRAFT, visibility=Dataset.Visibility.RESTRICTED,
                category="Agriculture", subject="Crop Yield",
                keywords=["ethiopia", "tabular"], languages=["English"],
                description="Agronomic and climate variables used to model teff crop yield across Ethiopian highland regions.",
                file_type="Excel", file_size=1_200_000, thumb_seed="agri-1",
                views=142, downloads=142,
            ),
            dict(
                title="Titles",
                owner="sosena.gossaye", status=Dataset.Status.REJECTED, visibility=Dataset.Visibility.RESTRICTED,
                category="Computer Science", subject="Crop Yield",
                keywords=[], languages=["English"],
                description="Placeholder rejected submission for testing status badges.",
                file_type="other", file_size=7_000, thumb_seed=None,
                views=0, downloads=0,
            ),
            dict(
                title="Highland Rainfall Variability Notes",
                owner="sosena.gossaye", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Environment", subject="Climate Modeling",
                keywords=[], languages=["English"],
                description="Field notes and rainfall variability observations across highland weather stations.",
                file_type="CSV", file_size=680_000, thumb_seed="climate-1",
                views=58, downloads=12,
            ),
            dict(
                title="Student Thesis Dataset - Soil Moisture Sensors",
                owner="sosena.gossaye", status=Dataset.Status.PENDING, visibility=Dataset.Visibility.RESTRICTED,
                category="Environment", subject="Soil Science",
                keywords=["sensor-data"], languages=["English"],
                description="Soil moisture sensor readings collected for undergraduate thesis fieldwork.",
                file_type="CSV", file_size=2_100_000, thumb_seed="soil-1",
                views=23, downloads=0,
            ),
            dict(
                title="Campus Energy Usage Draft",
                owner="sosena.gossaye", status=Dataset.Status.DRAFT, visibility=Dataset.Visibility.RESTRICTED,
                category="Environment", subject="Air Quality",
                keywords=["time-series"], languages=["English"],
                description="Preliminary campus building energy consumption logs, still being cleaned.",
                file_type="CSV", file_size=340_000, thumb_seed="air-quality-1",
                views=4, downloads=0,
            ),
            dict(
                title="Local Market Price Survey - Pilot",
                owner="sosena.gossaye", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.INSTITUTIONAL,
                category="Statistics", subject="Census Demographics",
                keywords=["survey-data", "ethiopia"], languages=["English", "Amharic"],
                description="Pilot price survey of staple goods across three local markets.",
                file_type="Excel", file_size=95_000, thumb_seed="census-1",
                views=76, downloads=19,
            ),
            dict(
                title="Bird Call Recordings - Campus Grounds",
                owner="sosena.gossaye", status=Dataset.Status.PUBLISHED, visibility=Dataset.Visibility.PUBLIC,
                category="Biology", subject="Biodiversity Survey",
                keywords=["audio-corpus"], languages=["English"],
                description="Short audio recordings of bird calls captured around campus grounds for a class project.",
                file_type="WAV", file_size=4_400_000, thumb_seed="biodiversity-1",
                views=31, downloads=8,
            ),
        ]

        created_datasets = []
        now = timezone.now()
        for i, spec in enumerate(dataset_specs):
            owner = by_username[spec["owner"]]
            dataset_defaults = dict(
                visibility=spec["visibility"],
                status=spec["status"],
                is_active=True,
                version=1,
                view_count=spec["views"],
                download_count=spec["downloads"],
                terms_accepted=True,
                terms_accepted_at=now,
                terms_version="1.0",
                thumbnail_key=thumb_for(spec["thumb_seed"]),
            )
            if spec["thumb_seed"]:
                dataset_defaults["thumbnail_source"] = Dataset.ThumbnailSource.UPLOADED

            dataset, _ = Dataset.objects.update_or_create(
                title=spec["title"],
                owner=owner,
                defaults=dataset_defaults,
            )
            if spec.get("force_created_today"):
                days_ago = 0
            else:
                days_ago = random.randint(1, 200)
            Dataset.objects.filter(pk=dataset.pk).update(
                created_at=now - timedelta(days=days_ago),
                updated_at=now - timedelta(days=random.randint(0, days_ago)),
            )

            lang_objs = [languages[n] for n in spec["languages"] if n in languages]
            dataset.languages.set(lang_objs)

            Metadata.objects.update_or_create(
                dataset=dataset,
                defaults=dict(
                    description=spec["description"],
                    category=categories[spec["category"]],
                    subject=subjects[spec["subject"]],
                    sponsor_or_grant="",
                ),
            )
            dataset.metadata.keywords.set([keywords[k] for k in spec["keywords"] if k in keywords])

            DatasetFile.objects.get_or_create(
                dataset=dataset,
                file_key=f"datasets/{dataset.id}/data.{spec['file_type'].lower().split(',')[0].strip()}",
                defaults=dict(
                    original_filename=f"{spec['title'][:40]}.{spec['file_type'].lower()}",
                    file_type=spec["file_type"],
                    file_size=spec["file_size"],
                    checksum=uuid.uuid4().hex,
                    is_structured=spec["file_type"].upper() in {"CSV", "JSON", "EXCEL", "TSV"},
                ),
            )

            extra_files = spec.get("extra_files", 0)
            for n in range(extra_files):
                DatasetFile.objects.get_or_create(
                    dataset=dataset,
                    file_key=f"datasets/{dataset.id}/supplement_{n + 1}.{spec['file_type'].lower().split(',')[0].strip()}",
                    defaults=dict(
                        original_filename=f"supplement_{n + 1}.{spec['file_type'].lower()}",
                        file_type=spec["file_type"],
                        file_size=max(int(spec["file_size"] * random.uniform(0.05, 0.3)), 1000),
                        checksum=uuid.uuid4().hex,
                        is_structured=spec["file_type"].upper() in {"CSV", "JSON", "EXCEL", "TSV"},
                    ),
                )

            Contributor.objects.get_or_create(
                dataset=dataset,
                user=owner,
                contributor_type=Contributor.ContributorType.OWNER,
                defaults=dict(name=owner.profile.full_name, order=1),
            )

            extra_contributor_username = spec.get("extra_contributor")
            if extra_contributor_username and extra_contributor_username in by_username:
                contributor_user = by_username[extra_contributor_username]
                Contributor.objects.get_or_create(
                    dataset=dataset,
                    user=contributor_user,
                    contributor_type=Contributor.ContributorType.CONTRIBUTOR,
                    defaults=dict(name=contributor_user.profile.full_name, order=2),
                )

            if spec["status"] == Dataset.Status.PUBLISHED:
                DatasetVersion.objects.get_or_create(
                    dataset=dataset,
                    version_number=1,
                    defaults=dict(
                        file_key=f"datasets/{dataset.id}/data.{spec['file_type'].lower().split(',')[0].strip()}",
                        source=DatasetVersion.Source.OWNER_EDIT,
                        changed_by=owner,
                        change_summary={"note": "Initial published version (seed data)."},
                    ),
                )

            created_datasets.append(dataset)

        return created_datasets

    def _seed_bookmarks(self, users, datasets):
        by_username = {u.username: u for u in users}
        published = [d for d in datasets if d.status == Dataset.Status.PUBLISHED]

        bookmark_plan = {
            "sosena.gossaye": 3,
            "sarah.jenkins": 2,
            "michael.chen": 2,
        }
        for username, count in bookmark_plan.items():
            user = by_username.get(username)
            if not user:
                continue
            for dataset in random.sample(published, k=min(count, len(published))):
                if dataset.owner_id == user.id:
                    continue
                Bookmark.objects.get_or_create(user=user, dataset=dataset)

    def _seed_activity_log(self, users, datasets):
        actions = [
            ("dataset.viewed", "viewed a dataset"),
            ("dataset.downloaded", "downloaded a dataset"),
            ("dataset.bookmarked", "bookmarked a dataset"),
            ("dataset.submitted", "submitted a dataset for review"),
        ]
        for _ in range(20):
            user = random.choice(users)
            dataset = random.choice(datasets)
            action, _label = random.choice(actions)
            ActivityLog.objects.create(
                user=user,
                action=action,
                target_object=f"dataset:{dataset.id}",
                ip_address="127.0.0.1",
                extra={"dataset_title": dataset.title},
            )