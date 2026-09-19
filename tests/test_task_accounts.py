import asyncio
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from server import ToolathlonGym


ROOT = Path(__file__).resolve().parents[1]


class TaskAccountsTest(unittest.IsolatedAsyncioTestCase):
    def test_all_account_files_can_supply_required_config_fields(self):
        for path in (ROOT / "tasks/finalpool").glob("*/email_config.json"):
            with self.subTest(task=path.parent.name):
                account = json.loads(path.read_text())
                if isinstance(account, list):
                    account = account[0]
                self.assertTrue(account.get("email"))
                self.assertTrue(account.get("password"))

    def test_explicit_sender_tasks_have_matching_accounts(self):
        for task in (ROOT / "tasks/finalpool").iterdir():
            config = task / "task_config.json"
            prompt = task / "docs/task.md"
            if not config.is_file() or not prompt.is_file():
                continue
            if "emails" not in json.loads(config.read_text()).get("needed_mcp_servers", []):
                continue
            senders = set(re.findall(
                r"\bfrom\s+([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", prompt.read_text(), re.I,
            ))
            if not senders:
                continue
            with self.subTest(task=task.name):
                account = json.loads((task / "email_config.json").read_text())
                if isinstance(account, list):
                    account = account[0]
                self.assertEqual(senders, {account["email"]})

    async def test_overlapping_episodes_pass_private_accounts_to_email_server(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environments = []
            for name in ["analytics", "wellness"]:
                task = root / "tasks" / name
                task.mkdir(parents=True)
                (task / "task_config.json").write_text(json.dumps({"needed_mcp_servers": ["emails"]}))
                (task / "email_config.json").write_text(json.dumps({"email": f"{name}@example.com"}))
                with patch("server.TASKS_ROOT", root / "tasks"):
                    environment = ToolathlonGym({"task_name": name})
                environment.workspace_dir = root / "workspaces" / name
                environment._psql = AsyncMock()
                environments.append(environment)

            process = SimpleNamespace(returncode=None)
            mcp_process = SimpleNamespace(initialize=AsyncMock(return_value=True), close=AsyncMock())
            with (
                patch("server.CONFIGS_DIR", ROOT / "configs/mcp_servers"),
                patch("server.ALL_TOOL_SCHEMAS", {"emails": []}),
                patch("server.run_preprocess", new_callable=AsyncMock),
                patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn,
                patch("server._MCPProcess", return_value=mcp_process),
            ):
                try:
                    await asyncio.gather(*(environment.setup() for environment in environments))
                    self.assertEqual(spawn.await_count, 2)
                    accounts = {}
                    for call in spawn.await_args_list:
                        arguments = call.args
                        account_path = Path(arguments[arguments.index("--config_file") + 1])
                        accounts[call.kwargs["env"]["PGDATABASE"]] = account_path
                    for environment in environments:
                        account_path = accounts[environment._db_name]
                        self.assertEqual(account_path, environment.task_dir / "email_config.json")
                        self.assertNotEqual(account_path.parent, environment._source_task_dir)
                        self.assertEqual(json.loads(account_path.read_text())["email"], f"{environment.task_name}@example.com")
                    await environments[0].teardown()
                    self.assertTrue(accounts[environments[1]._db_name].is_file())
                finally:
                    for environment in environments:
                        await environment.teardown()


if __name__ == "__main__":
    unittest.main()
