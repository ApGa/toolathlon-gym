import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from server import MCPBridge


ROOT = Path(__file__).resolve().parents[1]
LOCAL_SERVERS = ROOT / "local_servers"
if not LOCAL_SERVERS.exists():
    LOCAL_SERVERS = Path("/opt/local_servers")
CLI_SERVER = LOCAL_SERVERS / "cli-mcp-server/src/cli_mcp_server/server.py"


class TerminalEnvironmentTest(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_captures_task_environment_before_mcp_activation(self):
        with TemporaryDirectory() as tmp:
            process = SimpleNamespace(returncode=None)
            tools = [{"name": "run_command", "inputSchema": {}}]
            mcp_process = SimpleNamespace(
                initialize=AsyncMock(return_value=True), list_tools=AsyncMock(return_value=tools), close=AsyncMock(),
            )
            with (
                patch("server.CONFIGS_DIR", ROOT / "configs/mcp_servers"),
                patch("server.ALL_TOOL_SCHEMAS", {"terminal": tools}),
                patch.dict(os.environ, {"PATH": "/task/venv/bin:/usr/bin", "VIRTUAL_ENV": "/task/venv"}),
                patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn,
                patch("server._MCPProcess", return_value=mcp_process),
            ):
                bridge = MCPBridge(["terminal"], tmp, {"PGDATABASE": "test_session"})
                await bridge.start()
                env = spawn.await_args.kwargs["env"]
                self.assertEqual(env["TOOLATHLON_TASK_PATH"], "/task/venv/bin:/usr/bin")
                self.assertEqual(env["TOOLATHLON_TASK_VIRTUAL_ENV"], "/task/venv")
                self.assertEqual(env["PGDATABASE"], "test_session")

    def test_direct_and_shell_commands_use_task_python_without_mutating_mcp(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            mcp_bin = root / "mcp-bin"
            mcp_bin.mkdir()
            executable = mcp_bin / "python"
            executable.write_text("#!/bin/sh\necho wrong-mcp-python\nexit 97\n")
            executable.chmod(0o755)
            task_path = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]
            mcp_path = str(mcp_bin) + os.pathsep + os.environ["PATH"]
            values = {
                "ALLOWED_DIR": str(workspace), "PATH": mcp_path, "VIRTUAL_ENV": "/mcp/venv",
                "TOOLATHLON_TASK_PATH": task_path, "TOOLATHLON_TASK_VIRTUAL_ENV": "/task/venv",
                "ALLOWED_COMMANDS": "python", "ALLOWED_FLAGS": "all", "ALLOW_SHELL_OPERATORS": "true",
            }
            with patch.dict(os.environ, values):
                spec = importlib.util.spec_from_file_location("test_terminal_executor", CLI_SERVER)
                module = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, {spec.name: module}):
                    spec.loader.exec_module(module)
                (workspace / "probe.py").write_text(
                    "import os,sys,openpyxl,psycopg2\nprint(sys.executable)\nprint(os.environ['VIRTUAL_ENV'])\n"
                )
                command = "python probe.py"
                # Both subprocess.run branches must restore the environment.
                for request in [command, command + " && " + command]:
                    result = module.executor.execute(request)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(sys.executable, result.stdout)
                    self.assertIn("/task/venv", result.stdout)
                self.assertEqual(os.environ["PATH"], mcp_path)
                self.assertEqual(os.environ["VIRTUAL_ENV"], "/mcp/venv")


class TerminalShellParsingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name)
        values = {
            "ALLOWED_DIR": self.tmp.name, "ALLOWED_COMMANDS": "python,echo,cat",
            "ALLOWED_FLAGS": "all", "ALLOW_SHELL_OPERATORS": "true",
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        }
        scope = patch.dict(os.environ, values)
        scope.start()
        self.addCleanup(scope.stop)
        spec = importlib.util.spec_from_file_location("test_terminal_parser", CLI_SERVER)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: self.module}):
            spec.loader.exec_module(self.module)

    def test_quoted_python_semicolon_comparisons_and_literal_operators_are_arguments(self):
        for enabled in [False, True]:
            self.module.executor.security_config.allow_shell_operators = enabled
            for command, expected in [
                ("python -c 'import sys; print(2 > 1); print(1 < 2)'", "True\nTrue\n"),
                ('python -c "print(2 > 1); print(1 < 2)"', "True\nTrue\n"),
                (r"echo \; \| \> \<", "; | > <\n"),
                ("echo 'a && b || c; d > e < f'", "a && b || c; d > e < f\n"),
            ]:
                with self.subTest(command=command, enabled=enabled):
                    result = self.module.executor.execute(command)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected)
        self.assertFalse((self.workspace / "1").exists())

    def test_real_operators_keep_allow_setting_and_validate_each_command(self):
        command = "python -c 'print(2 > 1); print(1 < 2)' | cat"
        result = self.module.executor.execute(command)
        self.assertEqual(result.stdout, "True\nTrue\n")
        self.assertEqual(self.module.executor.execute("echo first; echo second").stdout, "first\nsecond\n")
        for operator in [";", "&&", "||", "|"]:
            with self.subTest(operator=operator), self.assertRaises(self.module.CommandSecurityError):
                self.module.executor.execute(f"echo harmless {operator} disallowed-command")
        self.module.executor.security_config.allow_shell_operators = False
        for value in [command, "echo first; echo second", "echo result > out.txt"]:
            with self.subTest(value=value), self.assertRaises(self.module.CommandSecurityError):
                self.module.executor.execute(value)

    def test_real_redirection_remains_distinct_from_quoted_filename_operator(self):
        self.module.executor.execute("echo first > 'report;one.txt'")
        self.module.executor.execute("echo second >> 'report;one.txt'")
        self.assertEqual((self.workspace / "report;one.txt").read_text(), "first\nsecond\n")
        with self.assertRaises(self.module.CommandSecurityError):
            self.module.executor.execute("echo 'unterminated; quote")


if __name__ == "__main__":
    unittest.main()
