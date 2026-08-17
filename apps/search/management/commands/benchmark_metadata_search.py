from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.utils import ProgrammingError

from apps.search.services import build_dataset_search_queryset


class Command(BaseCommand):
    help = "Benchmark metadata search with the current GIN-backed query path."

    def add_arguments(self, parser):
        parser.add_argument("query", nargs="?", default="cancer", help="Search term to benchmark")

    def _explain(self, queryset, sql_settings=None):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 0")
                if sql_settings:
                    for setting_sql in sql_settings:
                        cursor.execute(setting_sql)
            return queryset.explain(analyze=True, buffers=True)

    def handle(self, *args, **options):
        query = options["query"]
        queryset = build_dataset_search_queryset(query=query, user=None)

        try:
            self.stdout.write(self.style.MIGRATE_HEADING("Default planner"))
            self.stdout.write(self._explain(queryset))

            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING("Seq-scan baseline"))
            baseline = self._explain(
                queryset,
                sql_settings=[
                    "SET LOCAL enable_indexscan = off",
                    "SET LOCAL enable_bitmapscan = off",
                    "SET LOCAL enable_seqscan = on",
                ],
            )
            self.stdout.write(baseline)
        except ProgrammingError as exc:
            self.stderr.write(
                "Benchmark could not run because the database schema is not migrated yet. "
                "Run `python manage.py migrate` first."
            )
            raise SystemExit(1) from exc