"""Graders must not pay an episode that does nothing.

The 2026-10-15 no-op audit called claim_done at once on every train task and
found 77 graders that paid for it. These tests pin the repairs:

* Placeholder tasks have no gradeable deliverable, so their graders fail closed.
* The seeded tests (they need TOOLATHLON_TEST_PG_DSN, like the other seeded
  tests) build each repaired task's untouched state the way an episode does:
  the seed database, the task's initial workspace and its own preprocess. The
  grade of that state must be 0 in both reward modes. They then add the
  deliverables behind every repaired check, written the way the task's tools
  write them, and require those checks to pass, so a correct solution keeps
  its credit. Where the task ships reference output or setup data that used to
  be injected, the full solution must still pass.

The seeded tests run every script in-process against a private copy of the
seed. They need a role that may create databases.
"""
import contextlib
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch
import uuid
from email.utils import formataddr

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "tasks" / "finalpool"
RUNTIME_PATCHES = ROOT / "runtime_patches"
LAUNCH_TIME = "2026-03-08 12:00:00"
PG_DSN = os.environ.get("TOOLATHLON_TEST_PG_DSN")

PLACEHOLDER_TASKS = [
    "academic-conference-planning-coordination",
    "course-enrollment-analytics-dashboard",
    "event-catering-logistics-optimization",
    "financial-audit-cross-system-reconciliation",
    "healthcare-patient-outcomes-analysis",
    "inventory-supply-chain-optimization",
    "learning-outcomes-assessment-workflow",
    "market-competitive-intelligence-report",
    "portfolio-diversification-analysis-engine",
    "product-quality-assurance-multi-batch",
    "research-data-archival-compliance",
    "research-grant-proposal-coordination",
    "student-success-tracking-multi-touch",
    "vendor-management-scorecard-system",
]


def rewards(code, output, res_log):
    """The reward the server pays for this grade, in both modes."""
    from server import _reward_for_evaluation
    result = {}
    for mode in ("binary", "partial"):
        with patch.dict(os.environ, {"OPENREWARD_REWARD_MODE": mode}):
            result[mode] = _reward_for_evaluation(1.0 if code == 0 else 0.0, Path(res_log), output)
    return result


def exit_code(value):
    if value is None:
        return 0
    if isinstance(value, int):
        return int(value)
    return 1


class Grade:
    def __init__(self, code, output, res_log):
        self.code = code
        self.output = output
        self.reward = rewards(code, output, res_log)
        lines = [line.strip() for line in output.splitlines()]
        self.passed = {line[len("[PASS] "):].split(":")[0].strip() for line in lines if line.startswith("[PASS] ")}
        self.failed = {line[len("[FAIL] "):].split(":")[0].strip() for line in lines if line.startswith("[FAIL] ")}


def run_script(script, args, connect=None):
    """Run a task script in-process the way the server runs it as a process."""
    import psycopg2
    argv = [str(script), *args]
    out = io.StringIO()
    cwd = os.getcwd()
    modules = set(sys.modules)
    path = list(sys.path)
    code = 0
    with contextlib.ExitStack() as stack:
        if connect is not None:
            stack.enter_context(patch.object(psycopg2, "connect", connect))
        stack.enter_context(patch.object(sys, "argv", argv))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(out))
        sys.path[:0] = [str(script.parent), str(RUNTIME_PATCHES)]
        os.chdir(script.parent)
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as error:
            code = exit_code(error.code)
        except Exception:
            traceback.print_exc(file=out)
            code = 1
        finally:
            os.chdir(cwd)
            sys.path[:] = path
            # Forget task-local modules so the next task cannot reuse them.
            task_dir = str(script.parent.parent)
            for name in set(sys.modules) - modules:
                if str(getattr(sys.modules[name], "__file__", "") or "").startswith(task_dir):
                    del sys.modules[name]
    return code, out.getvalue()


def new_workspace(task_dir, workspace):
    """Lay out the agent workspace as the server does before preprocessing."""
    workspace.mkdir(parents=True)
    initial = task_dir / "initial_workspace"
    if initial.exists():
        for item in initial.iterdir():
            if item.is_dir():
                shutil.copytree(item, workspace / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, workspace / item.name)
    for name in ("arxiv_local_storage", "memory", ".playwright_output"):
        (workspace / name).mkdir(exist_ok=True)


def rewrite_empty_memory(workspace):
    """Leave memory.json as the memory server does after a call that adds nothing.

    For a .json path its saveGraph keeps the file's other keys and writes the
    graph back pretty-printed, so the file changes but still holds no memory.
    """
    path = workspace / "memory" / "memory.json"
    existing = json.loads(path.read_text() or "{}")
    rest = {key: value for key, value in existing.items() if key not in ("entities", "relations")}
    path.write_text(json.dumps({**rest, "entities": [], "relations": []}, indent=2) + "\n")


def grade(task_dir, workspace, connect=None):
    res_log = workspace / "eval_result.json"
    if res_log.exists():
        res_log.unlink()
    code, output = run_script(task_dir / "evaluation" / "main.py", [
        "--agent_workspace", str(workspace),
        "--groundtruth_workspace", str(task_dir / "groundtruth_workspace"),
        "--launch_time", LAUNCH_TIME,
        "--res_log_file", str(res_log),
    ], connect)
    return Grade(code, output, res_log)


class PlaceholderTaskTests(unittest.TestCase):
    def test_placeholder_graders_fail_closed(self):
        for task in PLACEHOLDER_TASKS:
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                task_dir = TASKS / task
                workspace = Path(tmp) / "workspace"
                new_workspace(task_dir, workspace)
                untouched = grade(task_dir, workspace)
                (workspace / "report.md").write_text("Conference plan, schedule and communications.\n")
                busy = grade(task_dir, workspace)
                for result in (untouched, busy):
                    self.assertEqual(result.code, 1, result.output)
                    self.assertEqual(result.reward, {"binary": 0.0, "partial": 0.0}, result.output)
                    self.assertIn("Placeholder task", result.output)


class MemoryOnlyGraderTests(unittest.TestCase):
    """arxiv-fetch-terminal-pipeline grades files only, so it needs no database."""

    def test_shipped_empty_memory_file_is_not_credited(self):
        task_dir = TASKS / "arxiv-fetch-terminal-pipeline"
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            new_workspace(task_dir, workspace)
            untouched = grade(task_dir, workspace)
            self.assertEqual(untouched.passed, set(), untouched.output)
            self.assertEqual(untouched.reward, {"binary": 0.0, "partial": 0.0}, untouched.output)

            rewrite_empty_memory(workspace)
            rewritten = grade(task_dir, workspace)
            self.assertEqual(rewritten.passed, set(), rewritten.output)
            self.assertEqual(rewritten.reward, {"binary": 0.0, "partial": 0.0}, rewritten.output)

            shutil.copy(task_dir / "groundtruth_workspace" / "memory" / "memory.json", workspace / "memory" / "memory.json")
            written = grade(task_dir, workspace)
            self.assertLessEqual({"memory.json exists", "Memory has content", "Memory is valid JSON",
                                  "Memory has paper entities (at least 3 keywords found)"}, written.passed, written.output)


# --------------------------------------------------------------------------
# Seeded tests


def load_seed(conn):
    """Restore db/init.sql.gz (minus role ownership) through psycopg2."""
    with conn.cursor() as cur, gzip.open(ROOT / "db" / "init.sql.gz", "rt", encoding="utf-8") as dump:
        statement = []
        for line in dump:
            if not statement and (not line.strip() or line.startswith("--")):
                continue
            if not statement and line.startswith("COPY ") and line.rstrip().endswith("FROM stdin;"):
                rows = []
                for row in dump:
                    if row == "\\.\n":
                        break
                    rows.append(row)
                cur.copy_expert(line.strip(), io.StringIO("".join(rows)))
                continue
            statement.append(line)
            if line.rstrip().endswith(";"):
                sql = "".join(statement)
                statement = []
                if " OWNER TO " not in sql:
                    cur.execute(sql)
        cur.execute((ROOT / "db" / "normalize_wc_categories.sql").read_text())
    conn.commit()


class Episode:
    """One task episode on a private database cloned from the seed."""

    template = None

    def __init__(self, task, tasks_root=TASKS):
        self.task = task
        self.source = tasks_root / task

    @staticmethod
    def dsn(dbname):
        import psycopg2.extensions
        # UTF8 whatever the cluster's encoding, as the other seeded tests set it:
        # on a SQL_ASCII cluster psycopg2 would encode with ASCII, and the seed's
        # non-ASCII rows could not load.
        return psycopg2.extensions.make_dsn(PG_DSN, dbname=dbname, client_encoding="UTF8",
                                            options="-c TimeZone=UTC")

    @classmethod
    def admin(cls, sql):
        import psycopg2
        conn = psycopg2.connect(cls.dsn(psycopg2.extensions.parse_dsn(PG_DSN).get("dbname", "postgres")))
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        finally:
            conn.close()

    @classmethod
    def create_template(cls):
        import psycopg2
        cls.template = f"toolathlon_noop_seed_{uuid.uuid4().hex[:8]}"
        cls.admin(f'CREATE DATABASE "{cls.template}"')
        conn = psycopg2.connect(cls.dsn(cls.template))
        try:
            load_seed(conn)
        finally:
            conn.close()

    @classmethod
    def drop_template(cls):
        if cls.template:
            cls.admin(f'DROP DATABASE IF EXISTS "{cls.template}" WITH (FORCE)')
            cls.template = None

    def connect(self, *args, **kwargs):
        return self._real_connect(self.dsn(self.db))

    def __enter__(self):
        import psycopg2
        self._real_connect = psycopg2.connect
        self.tmp = Path(tempfile.mkdtemp(prefix="toolathlon-noop-"))
        self.task_dir = self.tmp / "task"
        shutil.copytree(self.source, self.task_dir)
        self.workspace = self.tmp / "workspace"
        new_workspace(self.task_dir, self.workspace)
        self.db = f"toolathlon_noop_{uuid.uuid4().hex[:12]}"
        self.admin(f'CREATE DATABASE "{self.db}" TEMPLATE "{self.template}"')
        self.conn = psycopg2.connect(self.dsn(self.db))
        self.conn.autocommit = True
        code, output = run_script(self.task_dir / "preprocess" / "main.py", [
            "--agent_workspace", str(self.workspace), "--launch_time", LAUNCH_TIME], self.connect)
        if code != 0:
            self.__exit__(None, None, None)
            raise AssertionError(f"preprocess failed for {self.task}:\n{output}")
        return self

    def __exit__(self, *exc):
        self.conn.close()
        self.admin(f'DROP DATABASE IF EXISTS "{self.db}" WITH (FORCE)')
        shutil.rmtree(self.tmp, ignore_errors=True)

    def grade(self):
        return grade(self.task_dir, self.workspace, self.connect)

    def sql(self, statement, params=None):
        with self.conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else None

    def module(self):
        """The task's grader as a module whose queries run on this episode."""
        spec = importlib.util.spec_from_file_location(f"grader_{uuid.uuid4().hex}", self.task_dir / "evaluation" / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # ---- agent deliverables, written the way the task's tools write them

    def send_email(self, to, subject, body="", sender=None):
        """emails-mcp PgSMTPBackend.send_email: a Sent message plus a sent_log row."""
        account_file = self.task_dir / "email_config.json"
        account = json.loads(account_file.read_text()) if account_file.exists() else {"email": "agent@company.com"}
        address = sender or account["email"]
        name = None if sender else account.get("name")
        from_addr = formataddr((name, address)) if name else address
        recipients = [to] if isinstance(to, str) else list(to)
        folder = self.sql("INSERT INTO email.folders (name) VALUES ('Sent') "
                          "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id")[0][0]
        message = self.sql(
            "INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, cc_addr, bcc_addr, "
            "date, body_text, is_read, size) VALUES (%s, %s, %s, %s, %s, '[]', '[]', NOW(), %s, TRUE, %s) RETURNING id",
            (folder, f"<{uuid.uuid4()}@test>", subject, from_addr, json.dumps(recipients), body, len(body)))[0][0]
        self.sql("INSERT INTO email.sent_log (message_id) VALUES (%s)", (message,))

    def create_event(self, summary, start, end, description=None):
        """Calendar MCP events.insert."""
        self.sql("INSERT INTO gcal.events (summary, description, start_datetime, end_datetime, attendees) "
                 "VALUES (%s, %s, %s, %s, '[]'::jsonb)", (summary, description, start, end))

    def create_form(self, title, questions):
        """google-forms-mcp create_form plus one add_*_question per question."""
        form = self.sql("INSERT INTO gform.forms (title, document_title, description) VALUES (%s, %s, %s) RETURNING id",
                        (title, title, ""))[0][0]
        for position, (question, kind, options) in enumerate(questions):
            self.sql("INSERT INTO gform.questions (form_id, title, question_type, required, config, position) "
                     "VALUES (%s, %s, %s, false, %s, %s)",
                     (form, question, kind, json.dumps({"options": options} if options else {}), position))
        return form

    @staticmethod
    def rich_text(text):
        return [{"type": "text", "text": {"content": text}}]

    def create_notion_database(self, title, properties=None):
        """Notion MCP create-a-database."""
        database = str(uuid.uuid4())
        self.sql("INSERT INTO notion.databases (id, object, title, description, properties, parent, is_inline, archived) "
                 "VALUES (%s, 'database', %s, '[]', %s, %s, false, false)",
                 (database, json.dumps(self.rich_text(title)), json.dumps(properties or {"Name": {"title": {}}}),
                  json.dumps({"type": "workspace", "workspace": True})))
        return database

    def create_notion_page(self, title, blocks=(), database=None, title_key="title", properties=None):
        """Notion MCP post-page with paragraph children."""
        page = str(uuid.uuid4())
        parent = {"type": "database_id", "database_id": database} if database else {"type": "workspace", "workspace": True}
        props = {title_key: {"title": self.rich_text(title)}, **(properties or {})}
        self.sql("INSERT INTO notion.pages (id, object, parent, properties, archived, in_trash) "
                 "VALUES (%s, 'page', %s, %s, false, false)", (page, json.dumps(parent), json.dumps(props)))
        self.append_blocks(page, blocks)
        return page

    def append_blocks(self, page, blocks):
        start = self.sql("SELECT COALESCE(MAX(position), -1) + 1 FROM notion.blocks WHERE parent_id = %s", (page,))[0][0]
        for position, text in enumerate(blocks, start):
            self.sql("INSERT INTO notion.blocks (id, object, parent_type, parent_id, type, has_children, archived, "
                     "in_trash, block_data, position) VALUES (%s, 'block', 'page_id', %s, 'paragraph', false, false, "
                     "false, %s, %s)",
                     (str(uuid.uuid4()), page, json.dumps({"paragraph": {"rich_text": self.rich_text(text)}}), position))

    def create_spreadsheet(self, title, sheets):
        """mcp-google-sheets: a spreadsheet, its sheets and 0-based cells."""
        spreadsheet = str(uuid.uuid4())
        self.sql("INSERT INTO gsheet.spreadsheets (id, title) VALUES (%s, %s)", (spreadsheet, title))
        for index, (name, rows) in enumerate(sheets.items()):
            sheet = self.sql('INSERT INTO gsheet.sheets (spreadsheet_id, title, "index", row_count, column_count) '
                             'VALUES (%s, %s, %s, 1000, 26) RETURNING id', (spreadsheet, name, index))[0][0]
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    text = "" if value is None else str(value)
                    self.sql("INSERT INTO gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value, "
                             "formatted_value) VALUES (%s, %s, %s, %s, %s, %s)", (spreadsheet, sheet, r, c, text, text))
        return spreadsheet

    def copy_groundtruth(self, *names):
        for name in names:
            source = self.task_dir / "groundtruth_workspace" / name
            target = self.workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source, target)

    def write(self, name, content):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2))


# ---- per-task deliverables behind the repaired checks


def fieldtrip(ep):
    ep.sql("INSERT INTO canvas.announcements (course_id, title, message, is_announcement, posted_at) "
           "VALUES (1, %s, %s, true, NOW())",
           ("Field Trip to Qufu, March 12-15", "We depart Beijing South on train G235 on March 12 and return on March 15."))


def team_trip(ep):
    ep.create_event("Team Trip Departure", "2026-03-10 06:30:00", "2026-03-10 07:30:00")
    ep.create_event("Arrival and Hotel Check-in", "2026-03-10 12:30:00", "2026-03-10 14:00:00")
    ep.create_event("Team Dinner", "2026-03-10 18:00:00", "2026-03-10 20:00:00")


def supplier_visit(ep):
    ep.send_email("shanghai_supplier@techworld.com", "Meeting request - March 10", "Proposed meeting at 15:00.")
    ep.send_email("gz_supplier@supplier.com", "Meeting request - Guangzhou visit", "Proposed meeting time.")
    ep.send_email("procurement@company.com", "Supplier visit plan", "See Supplier_Visit_Plan.xlsx.")


PAPER_PAGES = [
    ("Paper: Scaling Laws for Neural Language Models", "Power-law scaling of loss with model size, data and compute."),
    ("Paper: Training language models to follow instructions with human feedback", "Three-step RLHF pipeline (InstructGPT)."),
    ("Paper: OPT: Open Pre-trained Transformer Language Models", "Open pre-trained transformer models matching GPT-3."),
]


def latex_analysis(ep):
    for title, notes in PAPER_PAGES:
        ep.create_notion_page(title, [notes])
    ep.copy_groundtruth("Paper_Analysis_Report.docx")
    ep.send_email("research_lead@university.edu", "Paper Analysis Report Ready",
                  "The Word report and the three Notion pages are ready for review.")


READING_PAPERS = [
    "Chain-of-Thought Reasoning in Large Language Models: A Survey",
    "Self-Consistency Improves Chain of Thought Reasoning",
    "Tree of Thoughts: Deliberate Problem Solving with LLMs",
    "Least-to-Most Prompting for Complex Reasoning Tasks",
    "ReAct: Synergizing Reasoning and Acting in LLMs",
]


def reading_group(ep):
    ep.create_form("Reading Group Paper Selection", [
        ("Which paper should we read first?", "RADIO", READING_PAPERS),
        ("What aspects interest you most?", "RADIO", ["Reasoning Methods", "Implementation", "Evaluation", "Theory"]),
        ("Availability for reading sessions", "CHECKBOX", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
    ])
    for number, day in enumerate([7, 14, 21, 28], 1):
        ep.create_event(f"Reading Group Session {number}", f"2026-04-{day:02d} 14:00:00", f"2026-04-{day:02d} 15:30:00")
    ep.copy_groundtruth("Reading_List.xlsx")


def catering(ep):
    ep.create_form("Menu Approval Survey", [
        ("How would you rate the overall menu selection?", "RADIO", ["Excellent", "Good", "Needs Improvement"]),
        ("Which dishes would you like to confirm or remove from the menu?", "PARAGRAPH", None),
        ("Please note any special dietary requirements or additional requests.", "PARAGRAPH", None),
    ])
    ep.copy_groundtruth("Catering_Plan.xlsx", "Catering_Proposal.docx")
    ep.send_email("client@corporate.com", "Catering Proposal for Team Building Event",
                  "The menu, the Excel plan, the Word proposal and the feedback form are ready.")


def recipe_kb(ep):
    ep.write("memory/memory.json", {"entities": [{
        "name": "recipe_selection_criteria", "entityType": "criteria",
        "observations": ["Selected categories: meat dishes, vegetable dishes, soups and staple foods"]}],
        "relations": []})


MEAL_PLAN = [
    (1, "Breakfast", "Toast with Jam", 15, "Easy"), (1, "Lunch", "Stir-fried Vegetables", 30, "Medium"),
    (1, "Dinner", "Cola Chicken Wings", 30, "Medium"), (2, "Breakfast", "Sunny-side Up Egg", 15, "Easy"),
    (2, "Lunch", "Tomato Beef Egg Drop Soup", 60, "Hard"), (2, "Dinner", "Mapo Tofu", 30, "Medium"),
    (3, "Breakfast", "Oatmeal with Eggs", 15, "Easy"), (3, "Lunch", "Braised Pork Belly", 60, "Hard"),
    (3, "Dinner", "Steamed Eggs", 15, "Easy"), (4, "Breakfast", "Milk Oatmeal", 15, "Easy"),
    (4, "Lunch", "Cold Cucumber Salad", 15, "Easy"), (4, "Dinner", "Sweet and Sour Pork", 60, "Hard"),
    (5, "Breakfast", "Boiled Corn", 15, "Easy"), (5, "Lunch", "Noodle Soup", 30, "Medium"),
    (5, "Dinner", "Village Beer Duck", 60, "Hard"), (6, "Breakfast", "Scallion Pancake", 30, "Medium"),
    (6, "Lunch", "Cold Tofu", 15, "Easy"), (6, "Dinner", "Kung Pao Chicken", 30, "Medium"),
    (7, "Breakfast", "Steamed Soft Egg", 15, "Easy"), (7, "Lunch", "Cream of Mushroom Soup", 15, "Easy"),
    (7, "Dinner", "Stir-fried Pork with Garlic Sprouts", 30, "Medium"),
]


def weekly_meals(ep):
    ep.create_spreadsheet("Weekly Meal Plan", {
        "Meal Plan": [["Day", "Meal_Type", "Recipe_Name", "Prep_Time", "Difficulty"], *MEAL_PLAN]})
    for day, _, recipe, _, _ in MEAL_PLAN[2::3]:
        ep.create_event(f"Dinner Prep - Day {day}", f"2026-04-{6 + day:02d} 18:00:00", f"2026-04-{6 + day:02d} 19:00:00",
                        f"Dinner: {recipe}")
    ep.send_email("meal_planning@service.com", "Weekly Meal Plan Ready",
                  "The 7-day meal plan with breakfast, lunch and dinner is in the Google Sheet; calendar events are set.")


def wellness(ep):
    ep.create_notion_page("Client Wellness Dashboard", [
        "Program summary", "Top recommended recipes", "Weekly meal plan overview",
        "Progress tracking guidance", "Daily targets: 5 vegetable servings, at most 25 g sugar"])


MANAGERS = {
    "Engineering": "sarahsmith@company.com", "Finance": "rohitsingh@company.com", "HR": "ananyataylor@company.com",
    "Operations": "ananyasingh@company.com", "R&D": "karengupta@company.com", "Sales": "kiranthompson@company.com",
    "Support": "davidthompson@company.com",
}


def department_review(ep):
    for dept, address in MANAGERS.items():
        ep.send_email(address, f"Department Performance Review - {dept}", f"{dept} review highlights.")


def multi_source(ep):
    ep.write("memory/memory.json", {"entities": [
        {"name": "Concrete Problems in AI Safety", "entityType": "paper", "observations": ["Amodei et al., 2016"]},
        {"name": "Risks from Learned Optimization", "entityType": "paper", "observations": ["mesa-optimization"]},
        {"name": "AI safety research tracking", "entityType": "research", "observations": ["Two search rounds done"]},
    ], "relations": []})


def competitor(ep):
    # The grader looks for "competitor" in the page title.
    ep.create_notion_page("Competitor Analysis - Q1 2026", ["Competitor summary and pricing recommendations."])
    ep.write("memory/memory.json", {"notes": ["Competitor analysis complete: TaskFlow Pro leads on price."]})


def onboarding(ep):
    ep.append_blocks("page-onboarding-checklist-001", [
        "March 2026 New Hires", "Sarah Chen, Mike Park and Amy Rodriguez start on March 16, 2026."])


RESTOCK_SUPPLIERS = {
    "trade@asiatech.com": "Asia Tech Trading", "b2b@smarthome-ws.com": "SmartHome Wholesale",
    "wholesale@digitaldreams.com": "Digital Dreams Supply",
}


def restock(ep):
    for address, name in RESTOCK_SUPPLIERS.items():
        ep.send_email(address, f"Restock Request - {name}", "Please restock our out-of-stock products.")


def health_dashboard(ep):
    with patch("psycopg2.connect", ep.connect):
        expected = ep.module().compute_expected_values(LAUNCH_TIME)
    for offset, row in enumerate(sorted(expected["below_15"], key=lambda r: float(r[6]))):
        day = 9 + offset // 8
        hour = 9 + offset % 8
        ep.create_event(f"Follow-up Call: {row[0]}", f"2026-03-{day:02d} {hour:02d}:00:00",
                        f"2026-03-{day:02d} {hour:02d}:30:00")


def performance_review_form(ep):
    ep.create_form("Annual Performance Review Form", [
        ("Employee ID", "SHORT_ANSWER", None),
        ("Department", "RADIO", ["Engineering", "Finance", "HR", "Operations", "R&D", "Sales", "Support"]),
        ("Performance Rating", "RADIO", ["1-Needs Improvement", "2-Below Expectations", "3-Meets Expectations",
                                         "4-Exceeds Expectations", "5-Outstanding"]),
        ("Key Achievements this year", "PARAGRAPH", None),
        ("Areas for Improvement", "PARAGRAPH", None),
        ("Goals for next year", "PARAGRAPH", None),
    ])
    ep.copy_groundtruth("Performance_Review_Setup.xlsx")
    ep.send_email("hr_director@company.com", "Annual Performance Review Process Setup Complete",
                  "Performance distribution by department; the review form and the Excel report are ready.")


def talent_review(ep):
    ep.send_email("hr_leadership@company.com", "Q1 2026 Talent Review Materials Ready",
                  "Headcount, performance rating, promotion candidates and salary findings.")


def discount_report(ep):
    rows = ep.sql('''
        SELECT c."SEGMENT", COUNT(o.*),
               COUNT(CASE WHEN o."DISCOUNT" > 0 THEN 1 END),
               ROUND(100.0 * COUNT(CASE WHEN o."DISCOUNT" > 0 THEN 1 END) / COUNT(*)::numeric, 1),
               ROUND(AVG(CASE WHEN o."DISCOUNT" > 0 THEN o."DISCOUNT" END)::numeric * 100, 2),
               ROUND(SUM(o."TOTAL_AMOUNT")::numeric, 2),
               ROUND(SUM(CASE WHEN o."DISCOUNT" > 0 THEN o."TOTAL_AMOUNT" ELSE 0 END)::numeric, 2),
               ROUND(100.0 * SUM(CASE WHEN o."DISCOUNT" > 0 THEN o."TOTAL_AMOUNT" ELSE 0 END)
                     / SUM(o."TOTAL_AMOUNT")::numeric, 1)
        FROM sf_data."SALES_DW__PUBLIC__ORDERS" o
        JOIN sf_data."SALES_DW__PUBLIC__CUSTOMERS" c ON o."CUSTOMER_ID" = c."CUSTOMER_ID"
        WHERE o."STATUS" = 'Delivered'
        GROUP BY c."SEGMENT" ORDER BY 4 DESC''')
    header = ["Segment", "Order_Count", "Discounted_Orders", "Discount_Rate_Pct", "Avg_Discount_Pct",
              "Total_Revenue", "Discounted_Revenue", "Revenue_Impact_Pct"]
    ep.create_spreadsheet("Discount Analysis Report", {"Segment Analysis": [header, *rows]})
    ep.send_email("finance-team@company.com", "Segment Discount Analysis", f"{rows[0][0]} uses discounts most often.")


def segment_report(ep):
    ep.copy_groundtruth("Q4_Segment_Report.docx")


def support_resolution(ep):
    by_type = ep.sql('''
        SELECT "ISSUE_TYPE", COUNT(*), COUNT(*) FILTER (WHERE "STATUS" = 'Closed'),
               ROUND(AVG(EXTRACT(EPOCH FROM ("RESOLVED_AT"::timestamp - "CREATED_AT"::timestamp)) / 3600)
                     FILTER (WHERE "RESOLVED_AT" IS NOT NULL)::numeric, 1),
               ROUND(AVG("CUSTOMER_SATISFACTION")::numeric, 2)
        FROM sf_data."SUPPORT_CENTER__PUBLIC__TICKETS" GROUP BY 1 ORDER BY 4 DESC''')
    by_priority = ep.sql('''
        SELECT "PRIORITY", COUNT(*), ROUND(AVG("RESPONSE_TIME_HOURS")::numeric, 1),
               ROUND(100.0 * COUNT(*) FILTER (WHERE "RESPONSE_TIME_HOURS" <= "SLA_HOURS") / COUNT(*), 1)
        FROM sf_data."SUPPORT_CENTER__PUBLIC__TICKETS" GROUP BY 1 ORDER BY 1''')
    ep.create_spreadsheet("Support Resolution Analysis", {
        "By Issue Type": [["Issue_Type", "Total_Tickets", "Resolved_Count", "Avg_Resolution_Hours", "Avg_Satisfaction"],
                          *by_type],
        "By Priority": [["Priority", "Ticket_Count", "Avg_Response_Hours", "SLA_Compliance_Pct"], *by_priority],
    })
    ep.send_email("support-lead@company.com", "Support Resolution Performance Report",
                  f"{by_type[0][0]} has the longest average resolution time; every priority meets its SLA.")


def curriculum_gap_sheet(ep):
    ep.create_spreadsheet("Curriculum Gap Analysis", {"Gaps": [["Topic", "Covered"], ["Large language models", "No"]]})


def research_pipeline(ep):
    database = ep.create_notion_database("Research Pipeline")
    ep.create_notion_page("Attention Is All You Need", database=database, title_key="Name")
    ep.send_email("portfolio_team@firm.com", "AI portfolio exposure", "AI exposure summary.")
    ep.send_email("risk_committee@firm.com", "AI sector risk", "Sector concentration risk.")


def risk_email(ep):
    ep.send_email("academic_advisors@university.edu", "Student Retention Risk Analysis - Action Required",
                  "12 high-risk students across both courses; schedule advising.")


def quiz_review_email(ep):
    ep.send_email("faculty@assessment.example.com", "Quiz Performance Analysis - Cross-Course Review",
                  "Quizzes reviewed, flagged for remediation; survey created and review sessions scheduled.")


def weekday_events(summary):
    def apply(ep):
        ep.create_event(summary, "2026-03-11 10:00:00", "2026-03-11 11:00:00")
    return apply


def accreditation_db(ep):
    database = ep.create_notion_database("Accreditation Action Items")
    ep.create_notion_page("Collect assessment evidence", database=database, title_key="Name")


def alignment_emails(ep):
    ep.send_email("dean@university.edu", "Research-Teaching Integration Summary", "Alignment scores for 4 faculty.")
    ep.send_email("head_biochem@university.edu", "Department Research-Teaching Alignment Update", "Biochemistry.")


def bookstore_emails(ep):
    ep.send_email("bookstore_manager@university.edu", "Student Electronics Purchase Behavior Report", "Correlation.")
    ep.send_email("academic_affairs@university.edu", "Academic Performance and Electronics Purchase Correlation",
                  "Correlation and mean score.")


def lunch_program(ep):
    ep.create_form("Employee Lunch Program Preferences", [
        ("Preferred cuisine type", "RADIO", ["Chinese Meat Dishes", "Chinese Vegetable Dishes", "Soups", "Seafood"]),
        ("Spice tolerance", "RADIO", ["Mild", "Medium", "Spicy"]),
        ("Dietary restrictions", "CHECKBOX", ["Vegetarian", "No Pork", "No Seafood", "Gluten Free", "No Restrictions"]),
        ("How important is variety in your lunch options?", "SCALE", None),
        ("Any additional food preferences or allergies?", "TEXT", None),
    ])
    ep.send_email("all_staff@company.com", "New Employee Lunch Program - Take Our Survey",
                  "The weekly menu has been planned; please fill out the dietary preference survey.")


def festival_events(ep):
    for day in range(9, 14):
        ep.create_event(f"Food Festival Prep - Day {day - 8}", f"2026-03-{day:02d} 09:00:00", f"2026-03-{day:02d} 11:00:00")


def nutrition_sheet(ep):
    ep.create_spreadsheet("Nutrition Dashboard", {"Recipes": [["Recipe", "Calories"], ["Tofu Salad", "320"]]})


def nutrition_workbook(ep):
    ep.copy_groundtruth("Nutrition_Program_Analysis.xlsx")


def training_survey(ep):
    ep.create_form("Training Feedback Survey", [
        ("Overall Training Satisfaction", "SCALE", None),
        ("Which module was most useful?", "TEXT", None),
        ("What improvements would you suggest?", "TEXT", None),
        ("Would you recommend this training to colleagues?", "RADIO", ["Yes", "No"]),
        ("Preferred training format", "RADIO", ["Online", "In-Person", "Hybrid"]),
    ])
    ep.send_email("hr_director@company.com", "Training Effectiveness Analysis - Key Findings",
                  "Performance rating improvement over baseline; survey satisfaction 3.9.")
    ep.send_email("training_team@company.com", "Training Feedback Survey Results",
                  "Average satisfaction 3.9; most preferred format Hybrid; 11 would recommend.")


def notion_database(title):
    def apply(ep):
        ep.create_notion_database(title)
    return apply


def hr_dashboard_page(ep):
    ep.create_notion_page("HR Dashboard - Employee Engagement", ["Engagement summary by department."])


def email_to(address, subject, body="Summary of the findings."):
    def apply(ep):
        ep.send_email(address, subject, body)
    return apply


def strategy_review(ep):
    ep.create_event("Q1 Sales Strategy Review", "2026-03-11 14:00:00", "2026-03-11 15:00:00")


def restock_event(ep):
    ep.create_event("Restock Review Meeting", "2026-03-12 10:00:00", "2026-03-12 11:00:00")


def meal_kit_survey(ep):
    ep.create_form("Meal Kit Interest Survey", [
        ("How often do you cook at home?", "RADIO", ["Daily", "Several times a week", "Once a week", "Rarely"]),
        ("What cuisine types interest you most?", "RADIO", ["Chinese", "Japanese", "Western", "All types"]),
        ("Which kitchen appliances do you currently own?", "CHECKBOX", ["Blender", "Electric Cooker", "None"]),
        ("What is your monthly budget for meal kits?", "RADIO", ["Under 30 dollars", "Over 100 dollars"]),
        ("How likely are you to purchase a meal kit bundled with an appliance?", "SCALE", None),
        ("What features would make a meal kit most appealing to you?", "TEXT", None),
    ])


def recall_emails(ep):
    ep.send_email("operations@company.com", "Product Recall Alert - Impact Summary", "Recalled products and orders.")
    ep.send_email("finance@company.com", "Recall Financial Impact Report", "Revenue at risk.")
    ep.send_email("legal@company.com", "Recall Customer Communication Plan", "Affected customers; notification plan.")


def supplier_quality_form(ep):
    ep.create_form("Supplier Quality Feedback", [("Rate supplier quality", "SCALE", None)])


def price_review_event(ep):
    ep.create_event("Price Review - Electronics", "2026-03-12 10:00:00", "2026-03-12 11:00:00")


def support_audit_emails(ep):
    ep.send_email("support_team@company.com", "Support Quality Audit - Priority Analysis", "Satisfaction by priority.")
    ep.send_email("product_team@company.com", "Support Quality Audit - Problem Products Alert", "Critical products.")


def pricing_sheet(ep):
    ep.create_spreadsheet("Commodity Pricing Dashboard", {"Prices": [["Commodity", "Price"], ["Copper", "4.1"]]})


def strategy_emails(ep):
    ep.send_email("ceo@company.com", "E-Commerce Market Strategy: Executive Summary", "Market conditions.")
    ep.send_email("marketing_team@company.com", "Category Performance Insights for Q2 Planning", "Categories.")


def wellness_events(ep):
    for summary, day in [("Healthy Breakfast Habits", "2026-03-19"), ("Plant-Based Lunch Ideas", "2026-03-25"),
                         ("Balanced Dinner Planning", "2026-04-02"), ("Meal Prep Workshop", "2026-04-08")]:
        ep.create_event(f"Wellness Workshop: {summary}", f"{day} 12:00:00", f"{day} 13:00:00")


def compensation_meeting(ep):
    ep.create_event("Q4 Compensation Review Meeting", "2026-03-09 13:00:00", "2026-03-09 14:30:00",
                    "Review market-adjusted sales bonuses for Q4")


def typescript_quiz(ep):
    quiz = ep.sql("INSERT INTO canvas.quizzes (course_id, title, quiz_type, time_limit, points_possible) "
                  "VALUES (1, 'TypeScript Essentials Quiz', 'assignment', 30, 50) RETURNING id")[0][0]
    for number in range(1, 11):
        ep.sql("INSERT INTO canvas.quiz_questions (quiz_id, question_name, question_type, question_text, "
               "points_possible, position) VALUES (%s, %s, 'multiple_choice_question', %s, 5, %s)",
               (quiz, f"Question {number}", f"TypeScript concept {number}?", number))


def survey_events(ep):
    ep.create_event("Science Communication Seminar", "2026-04-10 14:00:00", "2026-04-10 16:00:00")
    ep.create_event("Survey Review Session", "2026-04-03 10:00:00", "2026-04-03 11:00:00")


def noise_guards(addresses):
    return {f"No email sent to noise recipient {address}" for address in addresses}


# task: (deliverables, checks that must pass afterwards, whether the full grade must pass)
REPAIRED = {
    "12306-canvas-fieldtrip-gcal-word-email": (fieldtrip, {"Canvas announcement for field trip created"}, False),
    "12306-howtocook-team-trip-catering-excel-gcal": (team_trip, {
        "GCal has >= 2 agent-created events on 2026-03-10",
        "GCal has agent-created trip events (departure/dinner/arrival)"}, False),
    "12306-wc-supplier-visit-excel-email-gcal": (supplier_visit, {
        "At least 2 emails sent total (supplier visits + summary)", "Email sent to shanghai_supplier@techworld.com"}, False),
    "arxiv-latex-analysis-notion-word": (latex_analysis, {
        "Notion has pages for all 3 papers", "At least 3 Notion pages created",
        "Email sent to research_lead@university.edu"}, False),
    "arxiv-team-reading-gform-gcal": (reading_group, set(), True),
    "howtocook-diet-gform-notion-excel": (None, set(), False),
    "howtocook-event-catering-excel-word": (catering, {
        "Menu Approval Survey form exists", "Form has at least 3 questions"}, False),
    "howtocook-notion-recipe-kb": (recipe_kb, {
        "memory.json exists", "memory.json readable", "Memory has entities",
        "Memory contains filtering criteria or category info"}, False),
    "howtocook-weekly-gsheet-gcal": (weekly_meals, set(), True),
    "howtocook-wellness-tracker": (wellness, {"Notion wellness page exists", "Notion page has >= 5 blocks"}, False),
    "hr-department-review": (department_review, {"No more than 2 unexpected email recipients"}, False),
    "memory-multi-source-research": (multi_source, {
        "memory.json exists", "Memory has content", "Memory is valid JSON", "Memory has at least 3 entities",
        "Memory has research tracking entity"}, False),
    "notion-fetch-competitor": (competitor, {
        "No unexpected Notion pages", "Memory file exists", "Memory file is valid JSON",
        "Memory file has been updated with notes", "Memory mentions analysis/competitor/complete"}, False),
    "notion-gform-gcal-onboarding": (onboarding, {
        "Notion blocks contain 'march 2026'", "Notion blocks mention new hire names (>=2)"}, False),
    "quarterly-sales-restock-review": (restock, {
        f"No email sent to {address} (no out-of-stock products)" for address in [
            "verkauf@euroelec.de", "supply@premiumgadgets.com", "sales@globalelec.com",
            "orders@avpartners.com", "orders@techworld-dist.com"]}, False),
    "sf-customer-health-dashboard": (health_dashboard, {
        "Follow-up events cover the critical customers", "Follow-up events ordered by health score"}, False),
    "sf-hr-performance-review-gform-email": (performance_review_form, set(), True),
    "sf-hr-talent-review-excel-word-gcal-email": (talent_review, {
        "Summary email sent to hr_leadership@company.com", "Noise email not deleted"}, False),
    "sf-sales-discount-gsheet-email": (discount_report, set(), True),
    "sf-sales-segment-word": (segment_report, {"Word file exists", "Document has at least one table"}, False),
    "sf-support-resolution-gsheet": (support_resolution, set(), True),
    "terminal-arxiv-canvas-gsheet-word-gcal": (curriculum_gap_sheet, {
        "No noise arxiv papers in gsheet (marine biodiversity, cave art)"}, False),
    "terminal-arxiv-yf-excel-word-notion-email": (research_pipeline, {
        "No noise arxiv papers in Notion tracker (DragGAN, Llama 2)",
        "No emails sent to noise recipients (team@firm, office@firm, social@firm)"}, False),
    "terminal-canvas-excel-word-notion-email": (risk_email, {
        "Email sent about risk analysis", "Email subject relevant", "No risk emails sent to wrong recipients"}, False),
    "terminal-canvas-gform-gcal-excel-email": (quiz_review_email, {
        "No email sent to noise recipients", "Email from coordinator@assessment.example.com"}, False),
    "terminal-canvas-gsheet-word-notion-gcal": (weekday_events("Academic Advising Session"), {
        "No advising events on weekends"}, False),
    "terminal-canvas-notion-study-gcal-excel": (weekday_events("Study Session - Applied Analytics"), {
        "No study session events on weekends"}, False),
    "terminal-canvas-pdf-excel-notion-gcal": (accreditation_db, {
        "No noise Notion pages in accreditation tracker (budget, migration, marketing)",
        "Accreditation DB is distinct from noise Project Milestones DB"}, False),
    "terminal-canvas-scholarly-gsheet-word-email": (alignment_emails, noise_guards([
        "newsletter@university.edu", "all-faculty@university.edu", "registrar@university.edu"]), False),
    "terminal-canvas-sf-excel-ppt-gcal": (weekday_events("Advisory Board Meeting"), {
        "No advisory events on weekends"}, False),
    "terminal-canvas-wc-excel-ppt-email": (bookstore_emails, noise_guards([
        "newsletter@university.edu", "all-staff@university.edu", "alumni@university.edu",
        "admissions@university.edu"]), False),
    "terminal-fetch-sf-hr-gcal-excel-email": (weekday_events("Salary Review Meeting - Engineering"), {
        "No review meetings on weekends"}, False),
    "terminal-howtocook-gform-excel-notion-email": (lunch_program, {
        "Lunch preference survey form exists", "Survey has 5 questions", "Has cuisine question",
        "Email about lunch program sent", "Email subject mentions lunch or survey",
        "No lunch emails to competitor addresses"}, False),
    "terminal-howtocook-pdf-excel-word-gcal": (festival_events, {
        "No food festival prep events outside March 9-13"}, False),
    "terminal-howtocook-pw-nutrition-gsheet-word": (nutrition_sheet, {
        "No duplicate Nutrition Dashboard spreadsheets"}, False),
    "terminal-howtocook-scholarly-excel-word-gform": (nutrition_workbook, {
        "No noise scholarly papers anywhere in Excel workbook"}, False),
    "terminal-sf-canvas-word-gform-email": (training_survey, {
        "Training Feedback Survey form exists", "Form has exactly 5 questions", "Q1 about satisfaction",
        "Q4 about recommendation", "Q5 about format", "No emails sent to noise recipients"}, False),
    "terminal-sf-fetch-excel-notion-gcal": (notion_database("SLA Compliance Dashboard"), {
        "No duplicate SLA databases in Notion"}, False),
    "terminal-sf-fetch-support-ppt-email": (email_to(
        "managers@support-team.example.com", "Support Performance Benchmark Analysis"), {
        "No benchmark emails to competitor addresses", "Email from analytics@support-team.example.com"}, False),
    "terminal-sf-hr-diversity-gform-excel-notion": (notion_database("Diversity Metrics Dashboard"), {
        "No duplicate Diversity Metrics databases"}, False),
    "terminal-sf-hr-excel-word-gcal-email": (email_to(
        "hr_leadership@company.com", "Annual Performance Review Cycle - Department Summary"), {
        "Email about performance review sent", "No performance review emails to competitor addresses"}, False),
    "terminal-sf-notion-gform-excel-email": (hr_dashboard_page, {"No duplicate Notion HR Dashboard pages"}, False),
    "terminal-sf-sales-pdf-excel-ppt-email": (email_to("regional_managers@company.com", "Q1 2025 Sales Territory Review"), {
        "Sales review email sent", "No sales emails to competitor addresses"}, False),
    "terminal-sf-scholarly-excel-ppt-gcal": (strategy_review, {
        "No strategy review events on weekends", "No duplicate strategy review events"}, False),
    "terminal-sf-wc-support-gsheet-word-email": (email_to("cs_leadership@company.com", "Q1 Support Quality Audit Results"), {
        "Audit email sent", "No audit emails to wrong recipients"}, False),
    "terminal-wc-excel-gsheet-gcal-email": (restock_event, {"No restock events before March 2026"}, False),
    "terminal-wc-howtocook-gform-excel-notion": (meal_kit_survey, {
        "Meal Kit Interest Survey form exists", "Survey has 6 questions", "Has cooking frequency question",
        "Has cuisine question", "Has appliance ownership question", "Has budget question"}, False),
    "terminal-wc-pdf-excel-ppt-email": (recall_emails, {
        "Email to operations sent", "Email to finance sent", "Email to legal sent",
        "No recall emails to noise recipients (team@, all@, accounts@, hr@)"}, False),
    "terminal-wc-pdf-excel-word-gform": (supplier_quality_form, {"No duplicate supplier quality forms"}, False),
    "terminal-wc-pw-pricing-excel-word-gcal": (price_review_event, {"No price review events before March 10"}, False),
    "terminal-wc-sf-notion-excel-email": (support_audit_emails, {"Email to support_team sent", "Email to product_team sent"}
                                          | noise_guards(["all-staff@company.com", "hr@company.com",
                                                          "newsletter@company.com", "finance@company.com"]), False),
    "terminal-wc-yf-commodity-gsheet-word": (pricing_sheet, {"No duplicate pricing spreadsheets"}, False),
    "terminal-wc-yf-ppt-notion-email": (strategy_emails, noise_guards([
        "all-staff@company.com", "hr@company.com", "newsletter@company.com", "sales_team@company.com"]), False),
    "terminal-yf-excel-word-gform-email": (email_to(
        "investment_committee@company.com", "Q1 2025 Portfolio Performance Review"), {
        "Portfolio review email sent", "No portfolio emails to wrong recipients"}, False),
    "terminal-yf-fetch-gsheet-excel-notion": (notion_database("Investment Research Log"), {
        "No duplicate Investment Research databases"}, False),
    "terminal-yf-howtocook-excel-word-gcal": (wellness_events, {
        "No duplicate wellness calendar events", "No wellness events on weekends",
        "No wellness events outside March 16 - April 10 range"}, False),
    "terminal-yf-scholarly-excel-word-email": (email_to(
        "research_committee@university.edu", "FinTech Research Findings: Market Efficiency"), {
        "No duplicate research emails"}, False),
    "terminal-yf-sf-gsheet-word-gcal": (compensation_meeting, {
        "No duplicate Compensation Review events", "No wrong event types (bonus payout, salary update, pay raise)",
        "Pre-existing calendar events preserved (>= 5)", "Compensation meeting scheduled"}, False),
    "terminal-yf-sf-market-correlation-ppt-email": (email_to(
        "cfo@company.com", "Market and Sales Correlation Analysis Report", "Revenue; see the PowerPoint report."), {
        "Email sent to cfo@company.com", "No correlation emails to wrong recipients"}, False),
    "terminal-yf-wc-excel-word-notion": (notion_database("Market Research Dashboard"), {
        "No duplicate Market Research databases"}, False),
    "yt-fireship-canvas-quiz-excel-gcal": (typescript_quiz, {
        "Canvas quiz with TypeScript in title exists", "Canvas quiz has >= 5 questions"}, False),
    "yt-veritasium-arxiv-survey-word-gcal-email": (survey_events, {
        "GCal has >= 2 agent-created events in April 2026",
        "GCal has >= 1 seminar or review event added by agent"}, False),
}


def reorder_follow_ups(ep):
    ep.sql("UPDATE gcal.events SET start_datetime = start_datetime + interval '30 days' "
           "WHERE id = (SELECT id FROM gcal.events ORDER BY start_datetime LIMIT 1)")


# Guards must still catch the mistake they guard against once the deliverable exists:
# task: (mistake added to the solved episode, check that must then fail)
GUARD_MISTAKES = {
    "terminal-arxiv-yf-excel-word-notion-email": (email_to("team@firm.com", "AI portfolio exposure"),
                                                  "No emails sent to noise recipients (team@firm, office@firm, social@firm)"),
    "terminal-canvas-wc-excel-ppt-email": (email_to("alumni@university.edu", "Student Electronics Purchase Behavior Report"),
                                           "No email sent to noise recipient alumni@university.edu"),
    "terminal-canvas-gform-gcal-excel-email": (email_to("all-staff@university.edu", "Quiz Performance Analysis"),
                                               "No email sent to noise recipients"),
    "terminal-canvas-gsheet-word-notion-gcal": (lambda ep: ep.create_event(
        "Academic Advising Session", "2026-03-14 10:00:00", "2026-03-14 11:00:00"), "No advising events on weekends"),
    "terminal-sf-fetch-excel-notion-gcal": (notion_database("SLA Breach Log"), "No duplicate SLA databases in Notion"),
    "terminal-howtocook-pdf-excel-word-gcal": (lambda ep: ep.create_event(
        "Food Festival Prep - Extra", "2026-03-20 09:00:00", "2026-03-20 10:00:00"),
        "No food festival prep events outside March 9-13"),
    "sf-customer-health-dashboard": (reorder_follow_ups, "Follow-up events ordered by health score"),
}


@unittest.skipUnless(PG_DSN, "Requires disposable PostgreSQL database")
class NoopSeededGradingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Episode.create_template()

    @classmethod
    def tearDownClass(cls):
        Episode.drop_template()

    def check_task(self, task):
        deliverables, expected, full_pass = REPAIRED[task]
        with Episode(task) as ep:
            untouched = ep.grade()
            self.assertEqual(untouched.reward, {"binary": 0.0, "partial": 0.0}, untouched.output)
            self.assertEqual(untouched.passed, set(), untouched.output)
            if (ep.workspace / "memory" / "memory.json").exists():
                # A memory call that adds nothing rewrites the shipped file but records nothing.
                rewrite_empty_memory(ep.workspace)
                rewritten = ep.grade()
                self.assertEqual(rewritten.reward, {"binary": 0.0, "partial": 0.0}, rewritten.output)
                self.assertEqual(rewritten.passed, set(), rewritten.output)
            if deliverables is None:
                return
            deliverables(ep)
            solved = ep.grade()
            self.assertLessEqual(expected, solved.passed, solved.output)
            if full_pass:
                self.assertEqual(solved.code, 0, solved.output)
                self.assertEqual(solved.reward["binary"], 1.0, solved.output)
            else:
                self.assertGreater(solved.reward["partial"], 0.0, solved.output)
            if task in GUARD_MISTAKES:
                mistake, guard = GUARD_MISTAKES[task]
                mistake(ep)
                self.assertIn(guard, ep.grade().failed)


def _add_task_tests():
    for task in REPAIRED:
        def test(self, task=task):
            self.check_task(task)
        test.__name__ = "test_" + task.replace("-", "_")
        setattr(NoopSeededGradingTests, test.__name__, test)


_add_task_tests()


if __name__ == "__main__":
    unittest.main()
