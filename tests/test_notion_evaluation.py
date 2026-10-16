import contextlib
import gzip
import importlib.util
import io
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("TOOLATHLON_TEST_PG_DSN")


@unittest.skipUnless(DSN, "Set TOOLATHLON_TEST_PG_DSN to a disposable PostgreSQL database")
class NotionEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg2.connect(DSN)
        self.addCleanup(self.connection.close)
        # Use the image's schema so a renamed or nonexistent column fails here.
        with gzip.open(ROOT / "db/init.sql.gz", "rt") as source:
            sql = source.read()
        with self.connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA notion")
            for table in ["pages", "blocks"]:
                ddl = re.search(rf"CREATE TABLE notion\.{table} \(.*?\);", sql, re.S)
                self.assertIsNotNone(ddl)
                cursor.execute(ddl.group())
            cursor.execute("INSERT INTO notion.pages (id, properties) VALUES ('health', %s)",
                           ('{"title": "Customer Health Dashboard"}',))
        path = ROOT / "tasks/finalpool/sf-customer-health-dashboard/evaluation/main.py"
        spec = importlib.util.spec_from_file_location("health_dashboard_grader", path)
        self.grader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.grader)

    def insert_block(self, parent, *, archived=False, in_trash=False):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO notion.blocks (parent_type, parent_id, type, block_data, archived, in_trash) "
                "VALUES ('page_id', %s, 'paragraph', %s, %s, %s)",
                (parent, '{"paragraph": {"rich_text": []}}', archived, in_trash),
            )

    def grade(self, passed, failed):
        with patch.object(self.grader.psycopg2, "connect", return_value=self.connection), contextlib.redirect_stdout(io.StringIO()):
            self.grader.check_notion()
        self.assertEqual(self.grader.PASS_COUNT, passed)
        self.assertEqual(self.grader.FAIL_COUNT, failed)
        # The grader closes the uncommitted connection, removing the test schema.

    def test_page_with_block_passes_against_seed_schema(self):
        self.insert_block("health")
        self.grade(2, 0)

    def test_empty_page_fails_without_exception(self):
        self.grade(1, 1)

    def test_other_pages_and_deleted_blocks_do_not_satisfy_check(self):
        self.insert_block("unrelated")
        self.insert_block("health", archived=True)
        self.insert_block("health", in_trash=True)
        self.grade(1, 1)


if __name__ == "__main__":
    unittest.main()
