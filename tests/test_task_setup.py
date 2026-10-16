import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from server import CallToolInput, PythonExecuteInput, ToolathlonGym
from task_setup import run_preprocess


class PreprocessTest(unittest.IsolatedAsyncioTestCase):
    async def run_script(self, root, code, timeout=3):
        script = root / "main.py"
        script.write_text(code)
        return await run_preprocess(
            script, root, root / "preprocess.log", task="test-task",
            session="test-session", launch_time="2026-03-08 10:00:00",
            env=dict(os.environ), timeout=timeout,
        )

    async def test_nonzero_exit_reports_task_session_and_stderr(self):
        with TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output), self.assertRaisesRegex(RuntimeError, "exit status 7") as error:
                await self.run_script(Path(tmp), "import sys\nprint('fixture failed', file=sys.stderr)\nsys.exit(7)\n")
            self.assertIn("fixture failed", str(error.exception))
            event = json.loads(output.getvalue())
            self.assertEqual(event["event"], "preprocess_failed")
            self.assertEqual(event["task"], "test-task")
            self.assertEqual(event["session"], "test-session")
            self.assertIn("fixture failed", event["output_tail"])

    async def test_failure_output_is_bounded_and_retains_tail(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as error:
                await self.run_script(Path(tmp), "import sys\nprint('x' * 100000)\nprint('last error', file=sys.stderr)\nsys.exit(1)\n")
            self.assertLess(len(str(error.exception)), 9000)
            self.assertIn("last error", str(error.exception))

    @staticmethod
    def running(pid):
        status = Path(f"/proc/{pid}/stat")
        try:
            return status.read_text().split(") ", 1)[1].split()[0] != "Z"
        except FileNotFoundError:
            return False

    async def assert_stopped(self, pid):
        for _ in range(50):
            if not self.running(pid):
                return
            await asyncio.sleep(0.02)
        self.fail(f"Diagnostic child {pid} survived preprocessing cleanup")

    async def test_timeout_stops_preprocessor_and_its_child(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            code = "import os, pathlib, subprocess, sys, time\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\npathlib.Path('pids').write_text(f'{os.getpid()} {child.pid}')\ntime.sleep(60)\n"
            with self.assertRaisesRegex(RuntimeError, "timeout"):
                await self.run_script(root, code, timeout=0.3)
            for pid in map(int, (root / "pids").read_text().split()):
                await self.assert_stopped(pid)

    async def test_background_child_does_not_hold_setup_open(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code = "import pathlib, subprocess, sys\nchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\npathlib.Path('child').write_text(str(child.pid))\n"
            await asyncio.wait_for(self.run_script(root, code), timeout=3)
            await self.assert_stopped(int((root / "child").read_text()))

    async def test_cancellation_stops_preprocessor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = asyncio.create_task(self.run_script(root, "import os, pathlib, time\npathlib.Path('pid').write_text(str(os.getpid()))\ntime.sleep(60)\n", timeout=60))
            try:
                for _ in range(100):
                    if (root / "pid").exists():
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue((root / "pid").exists())
                pid = int((root / "pid").read_text())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await self.assert_stopped(pid)


class EnvironmentSetupTest(unittest.IsolatedAsyncioTestCase):
    def make_task(self, root, name, script, *, mcp=False):
        task = root / "tasks" / name
        for subdir in ["docs", "preprocess", "initial_workspace", "files"]:
            (task / subdir).mkdir(parents=True)
        (task / "task_config.json").write_text(json.dumps({
            "needed_mcp_servers": ["example"] if mcp else [],
            "http_fixture": {"port": 30180, "source": "files"},
        }))
        (task / "docs/task.md").write_text("Read http://localhost:30180/data.json")
        (task / "initial_workspace/params.json").write_text('{"url":"http://localhost:30180/data.json"}')
        (task / "files/data.json").write_text(json.dumps({"owner": name}))
        (task / "preprocess/main.py").write_text(script)
        return task

    def environment(self, root, name):
        with patch("server.TASKS_ROOT", root / "tasks"):
            env = ToolathlonGym({"task_name": name})
        env.workspace_dir = root / "workspaces" / env._db_name
        env._psql = AsyncMock()
        return env

    async def test_setup_copies_task_and_rewrites_inputs_without_mutating_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_task(root, "good", "from pathlib import Path\nPath(__file__).parent.joinpath('scratch').write_text('ok')\n")
            env = self.environment(root, "good")
            try:
                await env.setup()
                self.assertNotEqual(env.task_dir, source)
                self.assertTrue((env.task_dir / "preprocess/scratch").exists())
                self.assertFalse((source / "preprocess/scratch").exists())
                self.assertIn(env._http_fixture.url, (env.task_dir / "docs/task.md").read_text())
                self.assertEqual(json.loads((env.workspace_dir / "params.json").read_text())["url"], env._http_fixture.url + "/data.json")
                self.assertIn("localhost:30180", (source / "initial_workspace/params.json").read_text())
                # A legacy literal passed through the Python tool reaches this
                # episode, including when the original port is shared by tasks.
                result = await env.python_execute(PythonExecuteInput(code="import urllib.request\nprint(urllib.request.urlopen('http://localhost:30180/data.json').read().decode())"))
                self.assertIn('"owner": "good"', result.blocks[0].text)
            finally:
                await env.teardown()
            self.assertFalse(env.workspace_dir.exists())
            self.assertFalse(env._session_dir.exists())

    async def test_failed_setup_is_not_admitted_and_cleans_every_resource(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            self.make_task(root, "bad", "raise RuntimeError('broken fixture setup')\n", mcp=True)
            env = self.environment(root, "bad")
            with patch("server.MCPBridge") as bridge, self.assertRaisesRegex(RuntimeError, "broken fixture setup"):
                await env.setup()
            bridge.assert_not_called()
            self.assertFalse(env.workspace_dir.exists())
            self.assertFalse(env._session_dir.exists())
            self.assertIsNone(env._http_fixture._server)
            calls = [call.args[0] for call in env._psql.await_args_list]
            self.assertTrue(any("CREATE DATABASE" in sql for sql in calls))
            self.assertTrue(any("DROP DATABASE" in sql for sql in calls))

    async def test_mcp_cleanup_error_still_releases_fixture_and_database(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_task(root, "good", "print('ready')\n")
            env = self.environment(root, "good")
            await env.setup()
            env._mcp_bridge = AsyncMock()
            env._mcp_bridge.close.side_effect = RuntimeError("MCP already dead")
            with self.assertRaisesRegex(RuntimeError, "MCP already dead"):
                await env.teardown()
            self.assertIsNone(env._http_fixture._server)
            self.assertFalse(env._session_dir.exists())
            self.assertTrue(any("DROP DATABASE" in call.args[0] for call in env._psql.await_args_list))

    async def test_mcp_cleanup_cancellation_still_releases_episode_resources(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_task(root, "good", "print('ready')\n")
            env = self.environment(root, "good")
            await env.setup()
            env._mcp_bridge = AsyncMock()
            env._mcp_bridge.close.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await env.teardown()
            self.assertIsNone(env._http_fixture._server)
            self.assertFalse(env.workspace_dir.exists())
            self.assertFalse(env._session_dir.exists())
            self.assertTrue(any("DROP DATABASE" in call.args[0] for call in env._psql.await_args_list))

    async def test_mcp_arguments_and_returned_document_urls_are_session_specific(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_task(root, "good", "print('ready')\n")
            env = self.environment(root, "good")
            try:
                await env.setup()
                env._task_tool_by_name = {"fetch.read": {"bare_name": "read", "server": "fetch"}}
                env._mcp_bridge = AsyncMock()
                env._mcp_bridge.call_tool.return_value = "PDF says: http://localhost:30180/data.json"
                result = await env.call_tool(CallToolInput(name="fetch.read", arguments={"url": "http://localhost:30180/data.json"}))
                env._mcp_bridge.call_tool.assert_awaited_once_with("read", {"url": env._http_fixture.url + "/data.json"}, server="fetch")
                self.assertEqual(result.blocks[0].text, "PDF says: " + env._http_fixture.url + "/data.json")
            finally:
                await env.teardown()


if __name__ == "__main__":
    unittest.main()
