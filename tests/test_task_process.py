import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

from task_process import run_task_process


class TaskProcessTest(unittest.IsolatedAsyncioTestCase):
    async def run_code(self, root, code, **kwargs):
        return await run_task_process(
            sys.executable, "-c", code, cwd=str(root), env=dict(os.environ), **kwargs,
        )

    async def assert_stopped(self, pid):
        for _ in range(100):
            try:
                state = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[0]
                if state == "Z":
                    return
            except FileNotFoundError:
                return
            await asyncio.sleep(0.02)
        self.fail(f"Tool process {pid} survived cancellation")

    async def test_timeout_stops_parent_and_child_without_stopping_peer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            peer = asyncio.create_task(self.run_code(root, "import time; time.sleep(.8); print('peer alive')", timeout=5))
            code = (
                "import os, pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "pathlib.Path('pids').write_text(f'{os.getpid()} {child.pid}')\n"
                "time.sleep(60)\n"
            )
            with self.assertRaises(asyncio.TimeoutError):
                await self.run_code(root, code, timeout=.4)
            for pid in map(int, (root / "pids").read_text().split()):
                await self.assert_stopped(pid)
            self.assertEqual((await peer).stdout, "peer alive\n")

    async def test_rollout_cancellation_stops_running_code(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = asyncio.create_task(self.run_code(
                root, "import os, pathlib, time; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(60)", timeout=60,
            ))
            try:
                for _ in range(100):
                    if (root / "pid").exists():
                        break
                    await asyncio.sleep(.02)
                self.assertTrue((root / "pid").exists())
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            await self.assert_stopped(int((root / "pid").read_text()))

    async def test_large_stdout_and_stderr_are_drained_but_not_retained(self):
        with TemporaryDirectory() as tmp:
            result = await self.run_code(Path(tmp),
                "import os; os.write(1, b'x' * 2000000); os.write(2, b'y' * 2000000)",
                timeout=5, output_limit=1024,
            )
        self.assertEqual(result.returncode, 0)
        for output in [result.stdout, result.stderr]:
            self.assertIn("Output truncated", output)
            self.assertLess(len(output), 1200)

    async def test_nonzero_exit_and_non_utf8_output_remain_observable(self):
        with TemporaryDirectory() as tmp:
            result = await self.run_code(Path(tmp), "import os, sys; os.write(2, b'bad\\xff'); sys.exit(7)", timeout=5)
        self.assertEqual(result.returncode, 7)
        self.assertIn("bad", result.stderr)
