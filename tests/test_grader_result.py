"""Partial rewards come only from what the grader reports.

Partial mode used to read <workspace>/eval_result.json, which the agent can
write, before the grader's output. 316 of the 503 graders never write that
file, so an agent that wrote {"accuracy": 100} got full partial reward even
when its grader failed. claim_done now gives every grader run a new result
path outside the workspace, so whatever the agent leaves in its workspace
cannot be read as the grade, in either reward mode.
"""
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from http_fixtures import copy_task
from server import ToolathlonGym, _reward_for_evaluation


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
            env._session_dir = root / "session"
            env._session_dir.mkdir()
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
