import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server import PythonExecuteInput, ToolathlonGym


ROOT = Path(__file__).resolve().parents[1]


class PythonExecutionArtifactsTest(unittest.IsolatedAsyncioTestCase):
    def environment(self, workspace):
        environment = ToolathlonGym.__new__(ToolathlonGym)
        environment.workspace_dir = workspace
        environment._pg_env = lambda: {}
        return environment

    async def test_source_is_hidden_but_workspace_imports_cwd_and_script_semantics_remain(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "helper.py").write_text("answer = 42\n")
            env = self.environment(workspace)
            with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}):
                result = await env.python_execute(PythonExecuteInput(code=(
                    "from __future__ import annotations\n"
                    "import json, os, pathlib, sys, helper\n"
                    "pathlib.Path('answer.txt').write_text(str(helper.answer))\n"
                    "print(json.dumps({'cwd': os.getcwd(), 'files': os.listdir(), "
                    "'source': __file__, 'argv': sys.argv, 'path0': sys.path[0], 'name': __name__}))\n"
                )))
            self.assertFalse(result.metadata)
            payload = json.loads(result.blocks[0].text.removeprefix("stdout:\n").strip())
            self.assertEqual(payload["cwd"], str(workspace))
            self.assertEqual(payload["path0"], str(workspace))
            self.assertEqual(payload["name"], "__main__")
            self.assertEqual(payload["argv"], [payload["source"]])
            self.assertEqual((workspace / "answer.txt").read_text(), "42")
            self.assertFalse(any(name.startswith("_exec_") or name == "exec.py" for name in payload["files"]))
            source = Path(payload["source"])
            self.assertNotEqual(source.parent, workspace)
            self.assertFalse(source.parent.exists())

    async def test_cancellation_removes_source_directory(self):
        with TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            env = self.environment(workspace)
            with patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}):
                call = asyncio.create_task(env.python_execute(PythonExecuteInput(code=(
                    "import pathlib, time\npathlib.Path('source.txt').write_text(__file__)\ntime.sleep(60)\n"
                ))))
                try:
                    for _ in range(100):
                        if (workspace / "source.txt").exists():
                            break
                        await asyncio.sleep(.02)
                    self.assertTrue((workspace / "source.txt").exists())
                    source = Path((workspace / "source.txt").read_text())
                    self.assertTrue(source.exists())
                finally:
                    call.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await call
                self.assertFalse(source.parent.exists())


class CanvasGraderImportsTest(unittest.TestCase):
    def test_direct_and_module_entrypoints_import_their_siblings(self):
        for task in ["canvas-exam-calendar-report", "canvas-pdf-grade-gsheet"]:
            root = ROOT / "tasks/finalpool" / task
            for command, cwd in [([str(root / "evaluation/main.py")], root / "evaluation"),
                                 (["-m", "evaluation.main"], root)]:
                with self.subTest(task=task, command=command):
                    proc = subprocess.run([sys.executable, *command, "--help"], cwd=cwd,
                                          text=True, capture_output=True, timeout=20)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn("--agent_workspace", proc.stdout)


if __name__ == "__main__":
    unittest.main()
