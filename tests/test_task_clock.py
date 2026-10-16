from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from server import ToolathlonGym


class TaskClockTest(unittest.TestCase):
    def test_prompt_exposes_grader_launch_time_without_legacy_placeholder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "example"
            (task / "docs").mkdir(parents=True)
            (task / "docs/agent_system_prompt.md").write_text("Use the workspace tools.")
            (task / "docs/task.md").write_text("Schedule a meeting seven days after launch.")
            with patch("server.TASKS_ROOT", root), patch("server._now", return_value=datetime(2026, 3, 8, 12, 30)):
                env = ToolathlonGym({"task_name": "example"})
            env._tool_catalog_block = lambda: ""
            prompt = env.get_prompt()[0].text
            self.assertIn(f"Task launch time: {env._launch_time}", prompt)
            self.assertIn("operating-system clock may differ", prompt)
            self.assertIn("Schedule a meeting seven days after launch", prompt)
