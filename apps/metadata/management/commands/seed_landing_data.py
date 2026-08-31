"""
Seed demo data for the ORDP frontend build-out.

Usage:
    python manage.py seed_landing_data
    python manage.py seed_landing_data --flush

Place this file at:
    apps/metadata/management/commands/seed_landing_data.py
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
    UserProfile,
    UserRole,
    ActivityLog,
)

from apps.metadata.models import (
    Category,
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


def thumb_for(seed):
    """
    Return a deterministic demo thumbnail URL.

    thumbnail_key is a CharField(blank=True), so an empty string is used
    when a dataset intentionally has no thumbnail.
    """
    if seed is None:
        return ""

    if USE_FULL_URLS_AS_KEYS:
        return f"https://picsum.photos/seed/{seed}/600/400"

    return f"thumbnails/{seed}.jpg"


class Command(BaseCommand):
    help = (
        "Seed demo data for the landing page, Datasets browse page, "
        "Researcher Dashboard, My Datasets, and Bookmarks."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help=(
                "Delete previously-seeded demo rows matched by "
                "@demo.ordp before reseeding."
            ),
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        with transaction.atomic():
            colleges, coes, departments = self._seed_org_structure()

            categories, keywords, languages, characteristics = (
                self._seed_taxonomy()
            )

            self._seed_fallback_thumbnails(categories)

            users = self._seed_users(departments, categories)

            datasets = self._seed_datasets(
                users,
                categories,
                keywords,
                languages,
            )

            self._seed_bookmarks(users, datasets)
            self._seed_activity_log(users, datasets)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(users)} users and {len(datasets)} datasets."
            )
        )

        self.stdout.write(
            self.style.WARNING(
                "Demo user password for all seeded accounts: DemoPass123!"
            )
        )

    # ------------------------------------------------------------------
    # FLUSH
    # ------------------------------------------------------------------

    def _flush(self):
        demo_users = User.objects.filter(email__endswith="@demo.ordp")

        # Delete datasets owned by demo users first.
        Dataset.objects.filter(owner__in=demo_users).delete()

        # Remove activity logs associated with demo users.
        ActivityLog.objects.filter(user__in=demo_users).delete()

        demo_users.delete()

        self.stdout.write(
            self.style.WARNING(
                "Flushed previously-seeded demo users and their datasets."
            )
        )

    # ------------------------------------------------------------------
    # ORGANIZATION STRUCTURE
    # ------------------------------------------------------------------

    def _seed_org_structure(self):
        college_names = [
            "College of Electrical & Mechanical Engineering",
            "College of Applied Sciences",
            "College of Architecture & Civil Engineering",
        ]

        colleges = [
            College.objects.get_or_create(name=name)[0]
            for name in college_names
        ]

        coe_names = [
            "Center of Excellence in AI & Robotics",
            "Center of Excellence in Biotechnology",
        ]

        coes = [
            CenterOfExcellence.objects.get_or_create(name=name)[0]
            for name in coe_names
        ]

        dept_specs = [
            (
                "Computer Science & Engineering",
                colleges[0],
                None,
            ),
            (
                "Electrical Engineering",
                colleges[0],
                None,
            ),
            (
                "Environmental Science",
                colleges[1],
                None,
            ),
            (
                "Biology",
                colleges[1],
                None,
            ),
            (
                "Civil Engineering",
                colleges[2],
                None,
            ),
            (
                "Urban Planning",
                colleges[2],
                None,
            ),
            (
                "AI & Robotics Research",
                None,
                coes[0],
            ),
            (
                "Applied Biotechnology",
                None,
                coes[1],
            ),
        ]

        departments = []

        for name, college, coe in dept_specs:
            department, _ = Department.objects.get_or_create(
                name=name,
                college=college,
                center_of_excellence=coe,
            )

            departments.append(department)

        return colleges, coes, departments

    # ------------------------------------------------------------------
    # TAXONOMY
    # ------------------------------------------------------------------

    def _seed_taxonomy(self):
        category_names = [
            "Computer Science",
            "Education",
            "Classification",
            "Computer Vision",
            "NLP",
            "Data Visualization",
            "Pre-Trained Model",
            "Agriculture",
            "Civil Engineering",
            "Biology",
            "Environment",
            "Statistics",
        ]

        categories = {
            name: Category.objects.get_or_create(
                name=name,
                defaults={"status": Category.Status.APPROVED},
            )[0]
            for name in category_names
        }

        keyword_words = [
            "ethiopia",
            "amharic",
            "sensor-data",
            "geospatial",
            "audio-corpus",
            "east-africa",
            "time-series",
            "survey-data",
            "climate",
            "genomics",
            "remote-sensing",
            "tabular",
        ]

        keywords = {
            word: Keyword.objects.get_or_create(word=word)[0]
            for word in keyword_words
        }

        language_names = [
            "English",
            "Amharic",
            "Oromo",
            "Tigrinya",
        ]

        languages = {
            name: Language.objects.get_or_create(
                name=name,
                defaults={"status": Language.Status.APPROVED},
            )[0]
            for name in language_names
        }

        characteristic_names = [
            "Well-documented",
            "Well-maintained",
            "Clean data",
            "Original",
            "High-quality notebooks",
        ]

        characteristics = {
            name: DatasetCharacteristic.objects.get_or_create(
                name=name,
                defaults={"status": DatasetCharacteristic.Status.APPROVED},
            )[0]
            for name in characteristic_names
        }

        return (
            categories,
            keywords,
            languages,
            characteristics,
        )

    # ------------------------------------------------------------------
    # FALLBACK THUMBNAILS
    # ------------------------------------------------------------------

    def _seed_fallback_thumbnails(self, categories):
        for index, (_, category) in enumerate(categories.items()):
            FallbackThumbnail.objects.get_or_create(
                category=category,
                image_key=thumb_for(f"fallback-{index}"),
            )

    # ------------------------------------------------------------------
    # USERS
    # ------------------------------------------------------------------

    def _seed_users(self, departments, categories):
        dept_by_name = {
            department.name: department
            for department in departments
        }

        specs = [
            (
                "sarah.jenkins",
                "Dr. Sarah Jenkins",
                UserProfile.Academia.RESEARCHER,
                "Environmental Science",
                UserProfile.AcademicTitle.DR,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "michael.chen",
                "Michael Chen",
                UserProfile.Academia.DATA_SCIENTIST,
                "AI & Robotics Research",
                UserProfile.AcademicTitle.MR,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "abebe.bekele",
                "Dr. Abebe Bekele",
                UserProfile.Academia.PROFESSOR,
                "Environmental Science",
                UserProfile.AcademicTitle.DR,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "nlp.lab",
                "AASTU NLP Lab",
                UserProfile.Academia.RESEARCHER,
                "Computer Science & Engineering",
                UserProfile.AcademicTitle.NONE,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "civil.dept",
                "Civil Engineering Dept",
                UserProfile.Academia.LECTURER,
                "Civil Engineering",
                UserProfile.AcademicTitle.ENG,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "urban.dept",
                "Urban Planning Dept",
                UserProfile.Academia.RESEARCHER,
                "Urban Planning",
                UserProfile.AcademicTitle.MR,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "biology.group",
                "Biology Research Group",
                UserProfile.Academia.RESEARCHER,
                "Biology",
                UserProfile.AcademicTitle.DR,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "stats.dept",
                "Statistics Department",
                UserProfile.Academia.LECTURER,
                "Civil Engineering",
                UserProfile.AcademicTitle.MS,
                True,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "sosena.gossaye",
                "Sosena Gossaye",
                UserProfile.Academia.STUDENT,
                None,
                UserProfile.AcademicTitle.NONE,
                False,
                UserRole.RoleChoice.PUBLIC,
            ),
            (
                "demo.reviewer",
                "Demo Reviewer",
                UserProfile.Academia.RESEARCHER,
                "Computer Science & Engineering",
                UserProfile.AcademicTitle.DR,
                True,
                UserRole.RoleChoice.REVIEWER,
            ),
            (
                "demo.admin",
                "Demo Administrator",
                UserProfile.Academia.RESEARCHER,
                "Computer Science & Engineering",
                UserProfile.AcademicTitle.DR,
                True,
                UserRole.RoleChoice.ADMIN,
            ),
        ]

        users = []

        for (
            username,
            full_name,
            academia,
            dept_name,
            title,
            complete,
            role,
        ) in specs:

            email = f"{username}@demo.ordp"

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                },
            )

            if created:
                user.set_password("DemoPass123!")
                user.email = email
                user.save()

            department = (
                dept_by_name.get(dept_name)
                if dept_name
                else None
            )

            highest_degree = (
                "phd"
                if title == UserProfile.AcademicTitle.DR
                else "master"
            )

            profile, _ = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    "full_name": full_name,
                    "academia": academia,
                    "academic_title": title,
                    "department": department,
                    "highest_degree": highest_degree,
                    "bio": (
                        f"{full_name} — demo seed profile "
                        f"for {academia}."
                    ),
                    "profile_visibility": "public",
                    "terms_accepted": complete,
                    "terms_accepted_at": (
                        timezone.now()
                        if complete
                        else None
                    ),
                    "email_verified": True,

                    # Current ORDP upload authorization model.
                    "can_upload_datasets": complete,
                    "upload_permission_revoked": False,

                    # Current field name.
                    "interests_completed": complete,
                },
            )

            # Interests now use metadata.Category directly.
            if complete:
                category_values = list(categories.values())

                if category_values:
                    sample_size = min(3, len(category_values))

                    profile.interests.set(
                        random.sample(
                            category_values,
                            k=sample_size,
                        )
                    )
            else:
                profile.interests.clear()

            # Current system roles.
            UserRole.objects.update_or_create(
                profile=profile,
                role=role,
            )

            users.append(user)

        return users

    # ------------------------------------------------------------------
    # DATASETS
    # ------------------------------------------------------------------

    def _seed_datasets(
        self,
        users,
        categories,
        keywords,
        languages,
    ):
        by_username = {
            user.username: user
            for user in users
        }

        dataset_specs = [
            {
                "title": "Ethiopian Agricultural Crop Yield 2010-2023",
                "owner": "abebe.bekele",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Agriculture",
                "keywords": [
                    "ethiopia",
                    "time-series",
                    "tabular",
                ],
                "languages": ["English"],
                "description": (
                    "Comprehensive agricultural statistics and "
                    "yield metrics across Ethiopian regional "
                    "states, 2010-2023."
                ),
                "file_type": "CSV",
                "file_size": 4_200_000,
                "thumb_seed": "agri-1",
                "views": 2400,
                "downloads": 2143,
                "extra_files": 2,
                "extra_contributor": "sosena.gossaye",
            },
            {
                "title": "Amharic Sentiment Analysis Corpus V2",
                "owner": "nlp.lab",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "NLP",
                "keywords": [
                    "amharic",
                    "east-africa",
                ],
                "languages": [
                    "Amharic",
                    "English",
                ],
                "description": (
                    "Annotated text corpora for sentiment "
                    "classification in Amharic-language media."
                ),
                "file_type": "JSON",
                "file_size": 12_800_000,
                "thumb_seed": "nlp-1",
                "views": 5100,
                "downloads": 1892,
            },
            {
                "title": "Addis Ababa Traffic Flow Sensor Data",
                "owner": "civil.dept",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.INSTITUTIONAL,
                "category": "Civil Engineering",
                "keywords": [
                    "sensor-data",
                    "geospatial",
                ],
                "languages": ["English"],
                "description": (
                    "Real-time traffic density, vehicle "
                    "classification, and congestion indices "
                    "across Addis Ababa."
                ),
                "file_type": "CSV",
                "file_size": 450_000_000,
                "thumb_seed": "traffic-1",
                "views": 890,
                "downloads": 856,
                "extra_files": 3,
            },
            {
                "title": "Ethiopian Urban Growth Patterns",
                "owner": "urban.dept",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Civil Engineering",
                "keywords": [
                    "geospatial",
                    "remote-sensing",
                ],
                "languages": ["English"],
                "description": (
                    "Satellite-derived urban expansion indices "
                    "and settlement tracking across major "
                    "Ethiopian cities."
                ),
                "file_type": "GeoJSON",
                "file_size": 1_200_000_000,
                "thumb_seed": "urban-1",
                "views": 14100,
                "downloads": 3450,
                "extra_contributor": "sarah.jenkins",
            },
            {
                "title": "Amharic Speech Recognition Corpus",
                "owner": "nlp.lab",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "NLP",
                "keywords": [
                    "amharic",
                    "audio-corpus",
                ],
                "languages": ["Amharic"],
                "description": (
                    "Multi-speaker, high-fidelity audio "
                    "recordings with manual transcriptions "
                    "for ASR training."
                ),
                "file_type": "WAV",
                "file_size": 15_400_000_000,
                "thumb_seed": "speech-1",
                "views": 11900,
                "downloads": 5120,
                "extra_files": 4,
            },
            {
                "title": "Rift Valley Biodiversity Data",
                "owner": "biology.group",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Biology",
                "keywords": [
                    "east-africa",
                    "remote-sensing",
                ],
                "languages": ["English"],
                "description": (
                    "Flora and fauna survey inventories across "
                    "the East African Rift system."
                ),
                "file_type": "CSV",
                "file_size": 15_600_000,
                "thumb_seed": "biodiversity-1",
                "views": 4700,
                "downloads": 1205,
            },
            {
                "title": "Soil Salinity Mapping - Afar Region",
                "owner": "abebe.bekele",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Environment",
                "keywords": [
                    "geospatial",
                    "climate",
                ],
                "languages": ["English"],
                "description": (
                    "Spatial distribution of soil salinity and "
                    "electrical conductivity across the Afar region."
                ),
                "file_type": "TIFF",
                "file_size": 320_000_000,
                "thumb_seed": None,
                "views": 1800,
                "downloads": 412,
            },
            {
                "title": "Addis Ababa Air Quality Index",
                "owner": "sarah.jenkins",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Environment",
                "keywords": [
                    "climate",
                    "time-series",
                ],
                "languages": ["English"],
                "description": (
                    "Hourly particulate matter (PM2.5, PM10) "
                    "and gaseous pollutant records for Addis Ababa."
                ),
                "file_type": "JSON",
                "file_size": 8_400_000,
                "thumb_seed": "air-quality-1",
                "views": 3200,
                "downloads": 928,
                "force_created_today": True,
            },
            {
                "title": "National Census Sample 2024",
                "owner": "stats.dept",
                "status": Dataset.Status.PENDING,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Statistics",
                "keywords": [
                    "survey-data",
                    "tabular",
                ],
                "languages": [
                    "English",
                    "Amharic",
                ],
                "description": (
                    "Anonymized household demographic "
                    "distributions and economic indicators, "
                    "2024 sample."
                ),
                "file_type": "CSV",
                "file_size": 85_000_000,
                "thumb_seed": "census-1",
                "views": 2310,
                "downloads": 0,
            },
            {
                "title": "Global Temperature Anomalies 1990-2023",
                "owner": "sarah.jenkins",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Environment",
                "keywords": [
                    "climate",
                    "time-series",
                ],
                "languages": ["English"],
                "description": (
                    "Monthly global surface temperature deviation "
                    "records, normalized against the 1951-1980 baseline."
                ),
                "file_type": "CSV",
                "file_size": 1_200_000,
                "thumb_seed": "climate-1",
                "views": 6400,
                "downloads": 1900,
            },
            {
                "title": "CRISPR Target Mapping Index",
                "owner": "michael.chen",
                "status": Dataset.Status.DRAFT,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Biology",
                "keywords": ["genomics"],
                "languages": ["English"],
                "description": (
                    "An in-progress index of candidate CRISPR "
                    "guide RNA target sites, pending validation."
                ),
                "file_type": "JSON",
                "file_size": 45_800_000,
                "thumb_seed": "crispr-1",
                "views": 0,
                "downloads": 0,
            },
            {
                "title": "Urban Mobility Survey 2024 (Raw)",
                "owner": "urban.dept",
                "status": Dataset.Status.PENDING,
                "visibility": Dataset.Visibility.INSTITUTIONAL,
                "category": "Civil Engineering",
                "keywords": ["survey-data"],
                "languages": ["English"],
                "description": (
                    "Raw, unprocessed survey responses on "
                    "commuting patterns and transit usage "
                    "across metro districts."
                ),
                "file_type": "ZIP",
                "file_size": 80_200_000,
                "thumb_seed": "mobility-1",
                "views": 0,
                "downloads": 0,
            },
            {
                "title": "Teff Yield Prediction Variables",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.DRAFT,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Agriculture",
                "keywords": [
                    "ethiopia",
                    "tabular",
                ],
                "languages": ["English"],
                "description": (
                    "Agronomic and climate variables used to "
                    "model teff crop yield across Ethiopian "
                    "highland regions."
                ),
                "file_type": "Excel",
                "file_size": 1_200_000,
                "thumb_seed": "agri-1",
                "views": 142,
                "downloads": 142,
            },
            {
                "title": "Titles",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.REJECTED,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Computer Science",
                "keywords": [],
                "languages": ["English"],
                "description": (
                    "Placeholder rejected submission for testing "
                    "status badges."
                ),
                "file_type": "other",
                "file_size": 7_000,
                "thumb_seed": None,
                "views": 0,
                "downloads": 0,
            },
            {
                "title": "Highland Rainfall Variability Notes",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Environment",
                "keywords": [],
                "languages": ["English"],
                "description": (
                    "Field notes and rainfall variability "
                    "observations across highland weather stations."
                ),
                "file_type": "CSV",
                "file_size": 680_000,
                "thumb_seed": "climate-1",
                "views": 58,
                "downloads": 12,
            },
            {
                "title": "Student Thesis Dataset - Soil Moisture Sensors",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.PENDING,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Environment",
                "keywords": ["sensor-data"],
                "languages": ["English"],
                "description": (
                    "Soil moisture sensor readings collected "
                    "for undergraduate thesis fieldwork."
                ),
                "file_type": "CSV",
                "file_size": 2_100_000,
                "thumb_seed": "soil-1",
                "views": 23,
                "downloads": 0,
            },
            {
                "title": "Campus Energy Usage Draft",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.DRAFT,
                "visibility": Dataset.Visibility.RESTRICTED,
                "category": "Environment",
                "keywords": ["time-series"],
                "languages": ["English"],
                "description": (
                    "Preliminary campus building energy "
                    "consumption logs, still being cleaned."
                ),
                "file_type": "CSV",
                "file_size": 340_000,
                "thumb_seed": "air-quality-1",
                "views": 4,
                "downloads": 0,
            },
            {
                "title": "Local Market Price Survey - Pilot",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.INSTITUTIONAL,
                "category": "Statistics",
                "keywords": [
                    "survey-data",
                    "ethiopia",
                ],
                "languages": [
                    "English",
                    "Amharic",
                ],
                "description": (
                    "Pilot price survey of staple goods across "
                    "three local markets."
                ),
                "file_type": "Excel",
                "file_size": 95_000,
                "thumb_seed": "census-1",
                "views": 76,
                "downloads": 19,
            },
            {
                "title": "Bird Call Recordings - Campus Grounds",
                "owner": "sosena.gossaye",
                "status": Dataset.Status.PUBLISHED,
                "visibility": Dataset.Visibility.PUBLIC,
                "category": "Biology",
                "keywords": ["audio-corpus"],
                "languages": ["English"],
                "description": (
                    "Short audio recordings of bird calls "
                    "captured around campus grounds for a class project."
                ),
                "file_type": "WAV",
                "file_size": 4_400_000,
                "thumb_seed": "biodiversity-1",
                "views": 31,
                "downloads": 8,
            },
        ]

        created_datasets = []
        now = timezone.now()

        for spec in dataset_specs:
            owner = by_username[spec["owner"]]

            dataset_defaults = {
                "visibility": spec["visibility"],
                "status": spec["status"],
                "is_active": True,
                "version": 1,
                "view_count": spec["views"],
                "download_count": spec["downloads"],
                "terms_accepted": True,
                "terms_accepted_at": now,
                "terms_version": "1.0",
                "thumbnail_key": thumb_for(
                    spec["thumb_seed"]
                ),
            }

            if spec["thumb_seed"]:
                dataset_defaults["thumbnail_source"] = (
                    Dataset.ThumbnailSource.UPLOADED
                )

            dataset, _ = Dataset.objects.update_or_create(
                title=spec["title"],
                owner=owner,
                defaults=dataset_defaults,
            )

            if spec.get("force_created_today"):
                days_ago = 0
            else:
                days_ago = random.randint(1, 200)

            updated_days_ago = (
                random.randint(0, days_ago)
                if days_ago > 0
                else 0
            )

            Dataset.objects.filter(pk=dataset.pk).update(
                created_at=now - timedelta(days=days_ago),
                updated_at=now - timedelta(
                    days=updated_days_ago
                ),
            )

            # Languages
            lang_objs = [
                languages[name]
                for name in spec["languages"]
                if name in languages
            ]

            dataset.languages.set(lang_objs)

            # Metadata
            Metadata.objects.update_or_create(
                dataset=dataset,
                defaults={
                    "description": spec["description"],
                    "category": categories[spec["category"]],
                    "sponsor_or_grant": "",
                },
            )

            # Keywords
            dataset.metadata.keywords.set(
                [
                    keywords[word]
                    for word in spec["keywords"]
                    if word in keywords
                ]
            )

            # Primary file
            extension = (
                spec["file_type"]
                .lower()
                .split(",")[0]
                .strip()
            )

            primary_file_key = (
                f"datasets/{dataset.id}/data.{extension}"
            )

            DatasetFile.objects.get_or_create(
                dataset=dataset,
                file_key=primary_file_key,
                defaults={
                    "original_filename": (
                        f"{spec['title'][:40]}.{extension}"
                    ),
                    "file_type": spec["file_type"],
                    "file_size": spec["file_size"],
                    "checksum": uuid.uuid4().hex,
                    "is_structured": (
                        spec["file_type"].upper()
                        in {
                            "CSV",
                            "JSON",
                            "EXCEL",
                            "TSV",
                        }
                    ),
                },
            )

            # Additional files
            extra_files = spec.get("extra_files", 0)

            for number in range(extra_files):
                supplement_key = (
                    f"datasets/{dataset.id}/"
                    f"supplement_{number + 1}.{extension}"
                )

                DatasetFile.objects.get_or_create(
                    dataset=dataset,
                    file_key=supplement_key,
                    defaults={
                        "original_filename": (
                            f"supplement_{number + 1}.{extension}"
                        ),
                        "file_type": spec["file_type"],
                        "file_size": max(
                            int(
                                spec["file_size"]
                                * random.uniform(0.05, 0.3)
                            ),
                            1000,
                        ),
                        "checksum": uuid.uuid4().hex,
                        "is_structured": (
                            spec["file_type"].upper()
                            in {
                                "CSV",
                                "JSON",
                                "EXCEL",
                                "TSV",
                            }
                        ),
                    },
                )

            # Owner contributor
            Contributor.objects.get_or_create(
                dataset=dataset,
                user=owner,
                contributor_type=Contributor.ContributorType.OWNER,
                defaults={
                    "name": owner.profile.full_name,
                    "order": 1,
                },
            )

            # Additional contributor
            extra_contributor_username = spec.get(
                "extra_contributor"
            )

            if (
                extra_contributor_username
                and extra_contributor_username in by_username
            ):
                contributor_user = by_username[
                    extra_contributor_username
                ]

                Contributor.objects.get_or_create(
                    dataset=dataset,
                    user=contributor_user,
                    contributor_type=(
                        Contributor.ContributorType.CONTRIBUTOR
                    ),
                    defaults={
                        "name": contributor_user.profile.full_name,
                        "order": 2,
                    },
                )

            # Published version
            if spec["status"] == Dataset.Status.PUBLISHED:
                DatasetVersion.objects.get_or_create(
                    dataset=dataset,
                    version_number=1,
                    defaults={
                        "file_key": primary_file_key,
                        "source": DatasetVersion.Source.OWNER_EDIT,
                        "changed_by": owner,
                        "change_summary": {
                            "note": (
                                "Initial published version "
                                "(seed data)."
                            )
                        },
                    },
                )

            created_datasets.append(dataset)

        return created_datasets

    # ------------------------------------------------------------------
    # BOOKMARKS
    # ------------------------------------------------------------------

    def _seed_bookmarks(self, users, datasets):
        by_username = {
            user.username: user
            for user in users
        }

        published = [
            dataset
            for dataset in datasets
            if dataset.status == Dataset.Status.PUBLISHED
        ]

        bookmark_plan = {
            "sosena.gossaye": 3,
            "sarah.jenkins": 2,
            "michael.chen": 2,
        }

        for username, count in bookmark_plan.items():
            user = by_username.get(username)

            if not user or not published:
                continue

            candidates = [
                dataset
                for dataset in published
                if dataset.owner_id != user.id
            ]

            if not candidates:
                continue

            for dataset in random.sample(
                candidates,
                k=min(count, len(candidates)),
            ):
                Bookmark.objects.get_or_create(
                    user=user,
                    dataset=dataset,
                )

    # ------------------------------------------------------------------
    # ACTIVITY LOG
    # ------------------------------------------------------------------

    def _seed_activity_log(self, users, datasets):
        actions = [
            (
                "dataset.viewed",
                "viewed a dataset",
            ),
            (
                "dataset.downloaded",
                "downloaded a dataset",
            ),
            (
                "dataset.bookmarked",
                "bookmarked a dataset",
            ),
            (
                "dataset.submitted",
                "submitted a dataset for review",
            ),
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
                extra={
                    "dataset_title": dataset.title,
                },
            )