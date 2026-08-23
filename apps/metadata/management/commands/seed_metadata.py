from django.core.management.base import BaseCommand
from apps.metadata.models import Category, Subject


CATEGORIES = [
    "Computer Science & AI", "Environmental Science", "Public Health",
    "Social Sciences", "Engineering & Technology", "Physics & Astronomy",
    "Life Sciences & Biology", "Earth & Geosciences", "Agricultural Science",
    "Materials Science", "Economics & Finance", "Education Research",
]

SUBJECTS = [
    "Machine Learning", "Climate & Weather", "Epidemiology",
    "Civil Engineering", "Electrical Engineering", "Software Engineering",
    "Data Science & Analytics", "Water Resources & Hydrology",
    "Crop Science & Agronomy", "Remote Sensing & GIS", "Renewable Energy",
    "Structural Engineering", "Biomedical Engineering",
    "Artificial Intelligence", "Natural Language Processing",
    "Computer Vision", "Internet of Things (IoT)",
]


class Command(BaseCommand):
    help = "Seeds the database with the standard set of dataset Categories and Subjects."

    def handle(self, *args, **options):
        created_categories = 0
        for name in CATEGORIES:
            _, created = Category.objects.get_or_create(
                name=name, defaults={"status": Category.Status.APPROVED}
            )
            if created:
                created_categories += 1

        created_subjects = 0
        for name in SUBJECTS:
            _, created = Subject.objects.get_or_create(name=name)
            if created:
                created_subjects += 1

        total_categories = Category.objects.count()
        total_subjects = Subject.objects.count()

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {created_categories} new categories ({total_categories} total) "
            f"and {created_subjects} new subjects ({total_subjects} total)."
        ))