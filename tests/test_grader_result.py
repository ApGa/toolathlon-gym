"""The reward comes only from what the grader reports, and the grader is
run where the agent cannot reach it.

Partial mode used to read <workspace>/eval_result.json, which the agent can
write, before the grader's output. 316 of the 503 graders never write that
file, so an agent that wrote {"accuracy": 100} got full partial reward even
when its grader failed. claim_done now gives every grader run a new result
path outside the workspace, so whatever the agent leaves in its workspace
cannot be read as the grade, in either reward mode.

The server also used to run the session's own writable copy of the grader,
which the agent could overwrite with a stub that always passes. claim_done
now runs the grader from the image's read-only task tree, supplying only the
workspace, the database and a fresh result directory, and it pauses the
agent's own processes while the grader runs, so neither a rewritten grader
nor a background writer can change the grade. Partial-mode stdout parsing
takes the grader's own last summary line, so agent-controlled text echoed
into the output cannot be read as the score.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import textwrap
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from http_fixtures import copy_task
from server import ToolathlonGym, _reward_for_evaluation, _score_from_eval_output


ROOT = Path(__file__).resolve().parents[1]
AGENT_CLAIM = {"accuracy": 100}

# A grader whose behaviour comes from behaviour.json next to it. It reports
# what it was given in grader_saw.json, in the agent workspace.
GRADER = """\
import argparse, json, os, sys
from pathlib import Path

parser = argparse.ArgumentParser()
for name in ("--agent_workspace", "--groundtruth_workspace", "--launch_time", "--res_log_file"):
    parser.add_argument(name)
args = parser.parse_args()
behaviour = json.loads(Path(__file__).with_name("behaviour.json").read_text())
res_log = Path(args.res_log_file)
workspace = Path(args.agent_workspace).resolve()
Path(workspace, "grader_saw.json").write_text(json.dumps({
    "res_log": str(res_log),
    "existed": os.path.lexists(res_log),
    "in_workspace": res_log.parent.resolve().is_relative_to(workspace),
}))
if behaviour.get("result") is not None:
    res_log.write_text(json.dumps(behaviour["result"]))
print(behaviour.get("output", ""))
sys.exit(behaviour["exit"])
"""


def with_python_on_path():
    """Make the server's python3 the interpreter running the tests."""
    return patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})


def reward_mode(mode):
    return patch.dict(os.environ, {"OPENREWARD_REWARD_MODE": mode})


def release_fifo(path):
    """Unblock a reader stuck opening ``path``, so a regression fails instead of hanging."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
    except OSError:
        pass


class ClaimDoneResultFileTest(unittest.IsolatedAsyncioTestCase):
    def make_task(self, root, behaviour):
        task = root / "tasks" / "graded"
        for subdir in ("docs", "evaluation", "initial_workspace", "groundtruth_workspace"):
            (task / subdir).mkdir(parents=True)
        (task / "task_config.json").write_text(json.dumps({"needed_mcp_servers": []}))
        (task / "docs" / "task.md").write_text("Write the report.")
        (task / "initial_workspace" / "notes.txt").write_text("seed\n")
        (task / "evaluation" / "main.py").write_text(GRADER)
        (task / "evaluation" / "behaviour.json").write_text(json.dumps(behaviour))
        return task

    async def claim(self, behaviour, mode, plant=None):
        """Grade one episode; ``plant(workspace)`` runs first, as the agent."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_task(root, behaviour)
            with patch("server.TASKS_ROOT", root / "tasks"):
                env = ToolathlonGym({"task_name": "graded"})
            env.workspace_dir = root / "workspaces" / env._db_name
            env._psql = AsyncMock()
            await env.setup()
            try:
                if plant is not None:
                    plant(env.workspace_dir)
                with with_python_on_path(), reward_mode(mode):
                    result = await env.claim_done()
                saw = json.loads((env.workspace_dir / "grader_saw.json").read_text())
                # The result directory is the grader's alone and is gone afterwards.
                self.assertFalse(saw["existed"], saw)
                self.assertFalse(saw["in_workspace"], saw)
                self.assertFalse(os.path.lexists(saw["res_log"]), saw)
                self.assertFalse(os.path.lexists(Path(saw["res_log"]).parent), saw)
                self.assertTrue(result.finished)
                return result
            finally:
                await env.teardown()

    @staticmethod
    def plant_claim(workspace):
        (workspace / "eval_result.json").write_text(json.dumps(AGENT_CLAIM))

    async def test_agent_written_result_earns_nothing(self):
        silent_fail = {"exit": 1, "output": "[FAIL] report missing"}
        for mode in ("partial", "binary"):
            with self.subTest(mode=mode):
                result = await self.claim(silent_fail, mode, plant=self.plant_claim)
                self.assertEqual(result.reward, 0.0)
                self.assertTrue(result.blocks[0].text.startswith("FAIL\n"))

    async def test_agent_written_result_is_ignored_when_the_grader_passes(self):
        def plant_zero(workspace):
            (workspace / "eval_result.json").write_text(json.dumps({"accuracy": 0}))

        for mode in ("partial", "binary"):
            with self.subTest(mode=mode):
                result = await self.claim({"exit": 0, "output": "all good"}, mode, plant=plant_zero)
                self.assertEqual(result.reward, 1.0)

    async def test_grader_written_result_still_counts(self):
        cases = [
            ({"exit": 1, "result": {"passed": 3, "total": 4}}, {"partial": 0.75, "binary": 0.0}),
            ({"exit": 0, "result": {"accuracy": 80}}, {"partial": 0.8, "binary": 1.0}),
            ({"exit": 1, "result": {"total_passed": 0, "total_checks": 5}}, {"partial": 0.0, "binary": 0.0}),
        ]
        for behaviour, expected in cases:
            for mode, reward in expected.items():
                with self.subTest(behaviour=behaviour, mode=mode):
                    result = await self.claim(behaviour, mode, plant=self.plant_claim)
                    self.assertAlmostEqual(result.reward, reward)

    async def test_grader_output_summary_still_counts(self):
        behaviour = {"exit": 1, "output": "Overall: 1/4 checks passed"}
        for mode, reward in (("partial", 0.25), ("binary", 0.0)):
            with self.subTest(mode=mode):
                result = await self.claim(behaviour, mode, plant=self.plant_claim)
                self.assertAlmostEqual(result.reward, reward)

    async def test_planted_links_directories_and_fifos_are_ignored(self):
        def symlink(workspace):
            target = workspace / "notes" / "claim.json"
            target.parent.mkdir()
            target.write_text(json.dumps(AGENT_CLAIM))
            (workspace / "eval_result.json").symlink_to(target)

        def directory(workspace):
            (workspace / "eval_result.json").mkdir()
            (workspace / "eval_result.json" / "eval_result.json").write_text(json.dumps(AGENT_CLAIM))

        def fifo(workspace):
            os.mkfifo(workspace / "eval_result.json")
            release = threading.Timer(10, release_fifo, args=(workspace / "eval_result.json",))
            release.daemon = True
            release.start()
            self.addCleanup(release.cancel)

        silent_fail = {"exit": 1, "output": "[FAIL] report missing"}
        for plant in (symlink, directory, fifo):
            with self.subTest(plant=plant.__name__):
                started = time.monotonic()
                result = await self.claim(silent_fail, "partial", plant=plant)
                self.assertLess(time.monotonic() - started, 10, "claim_done blocked on the planted path")
                self.assertEqual(result.reward, 0.0)

    async def test_unwritable_result_location_fails_closed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_task(root, {"exit": 0, "output": "all good"})
            with patch("server.TASKS_ROOT", root / "tasks"):
                env = ToolathlonGym({"task_name": "graded"})
            env.workspace_dir = root / "workspaces" / env._db_name
            env._psql = AsyncMock()
            await env.setup()
            try:
                with with_python_on_path(), reward_mode("partial"), \
                        patch("server.tempfile.mkdtemp", side_effect=PermissionError("denied")):
                    result = await env.claim_done()
                self.assertEqual(result.reward, 0.0)
                self.assertTrue(result.finished)
                self.assertIn("Evaluation error", result.blocks[0].text)
            finally:
                await env.teardown()


class RealGraderTest(unittest.IsolatedAsyncioTestCase):
    """A real grader that never writes a result file: arxiv-fetch-terminal-pipeline.

    It grades workspace files only, so it runs without a database. Its untouched
    episode scores 0 in both modes; the planted claim used to pay 1.0 in partial mode.
    """

    async def test_planted_claim_does_not_pay_an_untouched_episode(self):
        source = ROOT / "tasks" / "finalpool" / "arxiv-fetch-terminal-pipeline"
        self.assertNotIn("open(args.res_log_file", (source / "evaluation" / "main.py").read_text())
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = ToolathlonGym.__new__(ToolathlonGym)
            env._db_name = "realgrader_test"
            env._session_dir = root / "session"
            env._session_dir.mkdir()
            # The server grades from the image's read-only task tree, never the
            # session's writable copy.
            env._source_task_dir = source
            env.task_dir = env._session_dir / "task"
            copy_task(source, env.task_dir)
            env.workspace_dir = root / "workspace"
            shutil.copytree(source / "initial_workspace", env.workspace_dir)
            for name in ("arxiv_local_storage", "memory", ".playwright_output"):
                (env.workspace_dir / name).mkdir(exist_ok=True)
            env._launch_time = "2026-03-08 12:00:00"
            env._pg_env = lambda: {"PYTHONPATH": str(ROOT / "runtime_patches")}
            (env.workspace_dir / "eval_result.json").write_text(json.dumps(AGENT_CLAIM))
            for mode in ("partial", "binary"):
                with self.subTest(mode=mode), with_python_on_path(), reward_mode(mode):
                    result = await env.claim_done()
                    self.assertTrue(result.blocks[0].text.startswith("FAIL\n"), result.blocks[0].text)
                    self.assertEqual(result.reward, 0.0, result.blocks[0].text)
            self.assertEqual(json.loads((env.workspace_dir / "eval_result.json").read_text()), AGENT_CLAIM)
            self.assertEqual([p.name for p in env._session_dir.iterdir()], ["task"])


PASS_STUB = "import sys\nprint('Overall: 1/1 checks passed')\nsys.exit(0)\n"

SLOW_FAIL_GRADER = """\
import argparse, sys, time
parser = argparse.ArgumentParser()
for name in ("--agent_workspace", "--groundtruth_workspace", "--launch_time", "--res_log_file"):
    parser.add_argument(name)
parser.parse_args()
time.sleep(float(%r))
print("REAL GRADER")
print("Overall: 0/3 checks passed")
sys.exit(1)
"""


def make_graded_task(root, grader_src):
    task = root / "tasks" / "graded"
    for subdir in ("docs", "evaluation", "initial_workspace", "groundtruth_workspace"):
        (task / subdir).mkdir(parents=True)
    (task / "task_config.json").write_text(json.dumps({"needed_mcp_servers": []}))
    (task / "docs" / "task.md").write_text("Write the report.")
    (task / "evaluation" / "main.py").write_text(grader_src)
    return task


async def build_env(root, grader_src):
    """A ready environment whose source grader is ``grader_src``; caller tears it down."""
    make_graded_task(root, grader_src)
    with patch("server.TASKS_ROOT", root / "tasks"):
        env = ToolathlonGym({"task_name": "graded"})
    env.workspace_dir = root / "workspaces" / env._db_name
    env._psql = AsyncMock()
    await env.setup()
    return env


class GraderRunsFromReadOnlyTreeTest(unittest.IsolatedAsyncioTestCase):
    """The grade comes from the image's grader, not the session's writable copy."""

    async def test_rewriting_the_session_copy_grader_earns_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = await build_env(root, SLOW_FAIL_GRADER % 0)
            try:
                # The agent overwrites every grader it can reach in its session
                # and plants a full-marks result file in its workspace.
                (env.task_dir / "evaluation" / "main.py").write_text(PASS_STUB)
                (env.workspace_dir / "eval_result.json").write_text(json.dumps({"accuracy": 100}))
                for mode in ("partial", "binary"):
                    with self.subTest(mode=mode), with_python_on_path(), reward_mode(mode):
                        result = await env.claim_done()
                    self.assertEqual(result.reward, 0.0, result.blocks[0].text)
                    self.assertIn("REAL GRADER", result.blocks[0].text)
                    self.assertTrue(result.blocks[0].text.startswith("FAIL\n"))
            finally:
                await env.teardown()

    async def test_background_process_rewriting_the_grader_earns_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = await build_env(root, SLOW_FAIL_GRADER % 0.6)
            session_grader = env.task_dir / "evaluation" / "main.py"
            stub = root / "stub.py"
            stub.write_text(PASS_STUB)
            # A background job the agent left running, hammering its session copy
            # of the grader for the whole grading window. It is NOT registered as
            # an agent process group, so it is not paused: only the read-only
            # grader tree protects the grade here.
            rewriter = subprocess.Popen(
                [sys.executable, "-c", textwrap.dedent(f"""
                    import shutil, time
                    for _ in range(100000):
                        shutil.copyfile({str(stub)!r}, {str(session_grader)!r})
                        time.sleep(0.005)
                """)],
                start_new_session=True,
            )
            try:
                with with_python_on_path(), reward_mode("partial"):
                    result = await env.claim_done()
                self.assertEqual(result.reward, 0.0, result.blocks[0].text)
                self.assertIn("REAL GRADER", result.blocks[0].text)
            finally:
                rewriter.terminate()
                rewriter.wait(timeout=5)
                await env.teardown()


class AgentProcessPauseTest(unittest.IsolatedAsyncioTestCase):
    """Pause the agent's own processes while a grader runs, then resume them."""

    def spawn(self, code, db):
        """A background process tagged with ``db`` in its environment, as the
        session's own subprocesses are tagged with PGDATABASE."""
        proc = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(code)],
            start_new_session=True,
            env={**os.environ, "PGDATABASE": db} if db else os.environ,
        )
        self.addCleanup(lambda: proc.poll() is None and os.killpg(os.getpgid(proc.pid), signal.SIGKILL))
        return proc

    async def test_this_sessions_process_is_frozen_then_resumed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = await build_env(root, SLOW_FAIL_GRADER % 0)
            counter = root / "counter"
            counter.write_text("0")
            worker = self.spawn(f"""
                import time
                n = 0
                while True:
                    n += 1
                    open({str(counter)!r}, "w").write(str(n))
                    time.sleep(0.005)
            """, env._db_name)
            try:
                # asyncio.sleep, not time.sleep: let wall-clock pass without
                # blocking the loop (and tripping its slow-callback warning).
                await asyncio.sleep(0.2)
                paused = env._pause_agent_processes()
                self.assertIn(worker.pid, paused)
                before = counter.read_text()
                await asyncio.sleep(0.4)
                self.assertEqual(counter.read_text(), before, "process kept running while paused")
                env._resume_processes(paused)
                await asyncio.sleep(0.2)
                self.assertNotEqual(counter.read_text(), before, "process did not resume")
            finally:
                os.killpg(os.getpgid(worker.pid), signal.SIGKILL)
                worker.wait(timeout=5)
                await env.teardown()

    async def test_other_sessions_and_the_worker_are_left_alone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = await build_env(root, SLOW_FAIL_GRADER % 0)
            other = self.spawn("import time; time.sleep(30)", "s_someoneelsesdb0")
            untagged = self.spawn("import time; time.sleep(30)", None)
            try:
                paused = env._pause_agent_processes()
                self.assertNotIn(other.pid, paused)
                self.assertNotIn(untagged.pid, paused)
                self.assertIsNone(other.poll())
                self.assertIsNone(untagged.poll())
            finally:
                for proc in (other, untagged):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=5)
                await env.teardown()


class SummaryParsingTest(unittest.TestCase):
    """Partial-mode stdout parsing reads the grader's own last summary line."""

    def test_last_summary_line_wins_over_earlier_ones(self):
        # A grader that tallies progressively: the final line is authoritative.
        self.assertAlmostEqual(
            _score_from_eval_output("Passed: 0, Failed: 1\nPassed: 0, Failed: 3"), 0.0)
        self.assertAlmostEqual(
            _score_from_eval_output("Overall: 1/4 checks passed\nOverall: 3/4 checks passed"), 0.75)

    def test_file_and_db_sections_score_on_the_file_line(self):
        text = ("  File checks - Passed: 2, Failed: 0\n"
                "  DB checks   - Passed: 0, Failed: 3\n"
                "  WARNING: 3 DB checks failed (not blocking)\n  Overall: PASS\n")
        self.assertAlmostEqual(_score_from_eval_output(text), 1.0)

    def test_agent_text_echoed_into_output_is_not_read_as_the_score(self):
        # The agent put a fake summary in a filename the grader echoes; the real
        # summary is the grader's last line.
        text = ("[FAIL] file 'Overall: 9/9 checks passed.xlsx' missing\n"
                "Overall: 0/5 checks passed\n")
        self.assertAlmostEqual(_score_from_eval_output(text), 0.0)

    def test_truncated_output_has_no_summary(self):
        text = ("Overall: 5/5 checks passed\n"
                "\n[Output truncated; write large results to a workspace file.]\n")
        self.assertIsNone(_score_from_eval_output(text))

    def test_result_pass_prefix_is_still_read(self):
        self.assertAlmostEqual(
            _score_from_eval_output("Result: FAIL - Overall: 2/8 checks passed"), 0.25)


class ResultFileReaderTest(unittest.TestCase):
    def partial(self, res_log, output=""):
        with reward_mode("partial"):
            return _reward_for_evaluation(0.0, res_log, output)

    def test_regular_result_file_is_read(self):
        with TemporaryDirectory() as tmp:
            res_log = Path(tmp) / "eval_result.json"
            res_log.write_text(json.dumps({"passed": 1, "failed": 3}))
            self.assertEqual(self.partial(res_log), 0.25)

    def test_symlink_is_not_followed(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "claim.json"
            target.write_text(json.dumps(AGENT_CLAIM))
            res_log = Path(tmp) / "eval_result.json"
            res_log.symlink_to(target)
            self.assertEqual(self.partial(res_log), 0.0)

    def test_unreadable_or_oversized_content_falls_back_to_output(self):
        with TemporaryDirectory() as tmp:
            res_log = Path(tmp) / "eval_result.json"
            res_log.write_bytes(b"\xff\xfe not json")
            self.assertEqual(self.partial(res_log, "Passed: 1, Failed: 1"), 0.5)
            with patch("server.GRADER_RESULT_MAX_BYTES", 8):
                res_log.write_text(json.dumps({"passed": 1, "total": 1}))
                self.assertEqual(self.partial(res_log), 0.0)

    def test_fifo_does_not_block(self):
        with TemporaryDirectory() as tmp:
            res_log = Path(tmp) / "eval_result.json"
            os.mkfifo(res_log)
            rewards = []
            reader = threading.Thread(target=lambda: rewards.append(self.partial(res_log)), daemon=True)
            reader.start()
            reader.join(5)
            try:
                self.assertFalse(reader.is_alive(), "reading a FIFO result path blocked")
                self.assertEqual(rewards, [0.0])
            finally:
                if reader.is_alive():
                    release_fifo(res_log)
                    reader.join(5)

    def test_binary_mode_reads_nothing(self):
        with TemporaryDirectory() as tmp:
            res_log = Path(tmp) / "eval_result.json"
            res_log.write_text(json.dumps({"passed": 1, "total": 4}))
            with reward_mode("binary"):
                self.assertEqual(_reward_for_evaluation(1.0, res_log, "Passed: 0, Failed: 4"), 1.0)
                self.assertEqual(_reward_for_evaluation(0.0, res_log, "Passed: 4, Failed: 0"), 0.0)


if __name__ == "__main__":
    unittest.main()
