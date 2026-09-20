import contextlib
import gzip
import importlib.util
import io
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openpyxl
import psycopg2


ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("TOOLATHLON_TEST_PG_DSN")


def load_grader(task):
    path = ROOT / "tasks/finalpool" / task / "evaluation/main.py"
    spec = importlib.util.spec_from_file_location("test_grader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(DSN, "Set TOOLATHLON_TEST_PG_DSN to a disposable PostgreSQL database")
class GraderRegressionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with gzip.open(ROOT / "db/init.sql.gz", "rt") as source:
            cls.seed = source.read()

    def setUp(self):
        self.conn = psycopg2.connect(DSN)
        self.conn.set_client_encoding("UTF8")
        self.addCleanup(self.conn.close)
        # Keep each test in a rollback-only transaction, even if a grader closes
        # its connection after reading a table.
        self.grader_conn = SimpleNamespace(cursor=self.conn.cursor, close=lambda: None)
        self.schemas = set()

    def table(self, name, *, copy_rows=False):
        schema = name.split(".")[0]
        with self.conn.cursor() as cur:
            if schema not in self.schemas:
                cur.execute(f"CREATE SCHEMA {schema}")
                self.schemas.add(schema)
            ddl = re.search(rf"CREATE TABLE {re.escape(name)} \(.*?\);", self.seed, re.S)
            cur.execute(ddl.group())
            if copy_rows:
                copy = re.search(rf"(COPY {re.escape(name)} .*? FROM stdin;)\n(.*?)\n\\\.", self.seed, re.S)
                cur.copy_expert(copy[1], io.StringIO(copy[2] + "\n"))

    def make_report(self, root):
        self.table("youtube.videos", copy_rows=True)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT video_id, title, published_at, duration, view_count, like_count
                FROM youtube.videos WHERE channel_title = 'Fireship'
                AND published_at >= '2024-01-01' AND published_at < '2026-01-01'
                ORDER BY view_count DESC, video_id LIMIT 10
            """)
            videos = cur.fetchall()
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Videos"
        ws.append(["Video_ID", "Title", "Published_Date", "Duration_Seconds", "View_Count", "Like_Count", "Primary_Topic"])
        summary = wb.create_sheet("Topic_Summary")
        summary.append(["Topic", "Video_Count", "Total_Views", "Avg_Duration"])
        aggregates = {}
        for i, (video_id, title, published, duration, views, likes) in enumerate(videos):
            topic = ["AI", "Security", "Systems"][i % 3]
            ws.append([video_id, title, published.date().isoformat(), int(duration), views, likes, topic])
            values = aggregates.setdefault(topic, [0, 0, 0])
            values[0] += 1
            values[1] += views
            values[2] += int(duration)
        for topic, (count, views, duration) in aggregates.items():
            summary.append([topic, count, views, duration / count])
        return wb

    def grade_report(self, root, wb):
        wb.save(root / "Tech_Trend_Report.xlsx")
        wb.close()
        grader = load_grader("yt-fireship-tech-report-excel-notion")
        with patch.object(grader.psycopg2, "connect", return_value=self.grader_conn), contextlib.redirect_stdout(io.StringIO()):
            grader.check_excel(str(root), str(ROOT / "tasks/finalpool/yt-fireship-tech-report-excel-notion/groundtruth_workspace"))
        return grader.FAIL_COUNT

    def test_report_from_real_seed_passes_despite_obsolete_reference_workbook(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wb = self.make_report(root)
            self.assertEqual(wb["Videos"]["A2"].value, "Nl7aCUsWykg")
            self.assertEqual(self.grade_report(root, wb), 0)

    def test_invented_reference_video_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wb = self.make_report(root)
            wb["Videos"]["A2"] = "dQw4w9WgXcQ"
            self.assertGreater(self.grade_report(root, wb), 0)

    def test_bad_video_values_and_topic_totals_are_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wb = self.make_report(root)
            wb["Videos"]["E2"] = 1
            wb["Topic_Summary"]["C2"] = 1
            self.assertGreaterEqual(self.grade_report(root, wb), 2)

    def test_missing_video_or_duplicate_topic_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wb = self.make_report(root)
            wb["Videos"].delete_rows(11)
            wb["Topic_Summary"].append(["AI", 4, 0, 0])
            self.assertGreater(self.grade_report(root, wb), 0)

    def test_support_resolution_queries_reach_file_grading_without_sql_exception(self):
        for name in ["gsheet.spreadsheets", "gsheet.sheets", "gsheet.cells", "email.messages"]:
            self.table(name)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO gsheet.spreadsheets (id, title) VALUES ('support', 'Support Resolution Analysis')")
            cur.execute("INSERT INTO gsheet.sheets (id, spreadsheet_id, title) VALUES (0, 'support', 'By Issue Type'), (1, 'support', 'By Priority')")
        grader = load_grader("sf-support-resolution-gsheet")
        with TemporaryDirectory() as tmp:
            with patch.object(grader.psycopg2, "connect", return_value=self.grader_conn), patch.object(sys, "argv", ["grader", "--agent_workspace", tmp]), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as result:
                    grader.main()
            self.assertEqual(result.exception.code, 1)
            self.assertIn("Support_Resolution_Analysis.xlsx not found", output.getvalue())
            self.assertIn("Checking By Priority", output.getvalue())
