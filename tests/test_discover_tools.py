"""The image build's tool discovery must record every configured MCP server or fail.

server.py offers each task only the tools in /app/tool_schemas.json, so a server that
discovery skips has no tools at run time. The c1e58af image shipped without its
snowflake entry because discovery logged a warning and wrote the rest.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import textwrap
import tomllib
import unittest
from unittest.mock import patch

import discover_tools

ROOT = Path(__file__).resolve().parents[1]

# A stdio MCP server: argv = mode, tools JSON. It also reports how it was launched.
FAKE_SERVER = textwrap.dedent('''
    import json, os, sys
    mode, tools = sys.argv[1], json.loads(sys.argv[2])
    if mode == "crash":
        print("fake server: cannot import its MCP SDK", file=sys.stderr, flush=True)
        sys.exit(3)
    launch = {"argv": sys.argv[3:], "cwd": os.getcwd(),
              "env": {k: os.environ.get(k) for k in ("PGDATABASE", "PG_DATABASE", "PGHOST", "PYTHONHASHSEED", "PYTHONPATH",
                                                      "FROM_YAML")}}
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg or mode == "silent":
            continue
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake", "version": "0"}}
        elif msg["method"] == "tools/list":
            if mode == "launch":
                tools = [{"name": "launch", "description": json.dumps(launch), "inputSchema": {"type": "object"}}]
            page = int((msg.get("params") or {}).get("cursor") or 0)
            size = 1 if mode == "paged" else len(tools) or 1
            result = {"tools": tools[page:page + size]}
            if page + size < len(tools):
                result["nextCursor"] = str(page + size)
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                              "error": {"code": -32601, "message": "no such method"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
''')

TOOLS = [
    {"name": "read_query", "description": "Execute a SELECT query.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "list_tables", "description": "List tables",
     "inputSchema": {"type": "object", "properties": {}}},
]


class DiscoverToolsTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.configs = self.root / "configs"
        self.configs.mkdir()
        self.server = self.root / "fake_server.py"
        self.server.write_text(FAKE_SERVER)
        self.output = self.root / "tool_schemas.json"

    def config(self, filename, name, mode="ok", tools=TOOLS, command=None, extra_args=(), env=None):
        params = {"command": command or sys.executable,
                  "args": [str(self.server), mode, json.dumps(tools), *extra_args]}
        if env:
            params["env"] = env
        (self.configs / filename).write_text(json.dumps({"type": "stdio", "name": name, "params": params}))

    def discover(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = discover_tools.main([
                "--configs", str(self.configs), "--output", str(self.output),
                "--workspace-root", str(self.root / "ws"), "--local-servers", str(self.root / "servers"),
                "--timeout", "20", "--init-retry", "5", "--attempts", "1", *extra,
            ])
        return code, out.getvalue(), err.getvalue()

    def test_records_every_configured_server_with_its_schemas(self):
        self.config("a.yaml", "alpha")
        self.config("b.yaml", "beta", mode="paged")
        umask = os.umask(0o077)  # a restrictive builder umask must not leave the catalog unreadable
        try:
            code, out, err = self.discover()
        finally:
            os.umask(umask)
        self.assertEqual(code, 0, out + err)
        catalog = json.loads(self.output.read_text())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o644, "readable by the server's uid")
        self.assertEqual(list(catalog), ["alpha", "beta"])
        self.assertEqual(catalog["alpha"], TOOLS)
        self.assertEqual(catalog["beta"], TOOLS, "every tools/list page is recorded")
        self.assertIn("Saved 2 servers, 4 tools", out)

    def test_a_configured_server_that_cannot_start_fails_without_writing(self):
        self.output.write_text('{"previous": []}')
        self.config("a.yaml", "alpha")
        self.config("s.yaml", "snowflake", command=str(self.root / "missing" / "uv"))
        code, out, err = self.discover()
        self.assertEqual(code, 1)
        self.assertIn("1 of 2 configured servers answered", err)
        self.assertIn("snowflake: cannot launch", err)
        self.assertEqual(self.output.read_text(), '{"previous": []}', "no partial catalog is written")

    def test_a_crashing_server_fails_and_its_stderr_says_why(self):
        self.config("a.yaml", "alpha")
        self.config("s.yaml", "snowflake", mode="crash")
        code, out, err = self.discover("--attempts", "2")
        self.assertEqual(code, 1)
        self.assertIn("snowflake:", err)
        self.assertIn("exited with status 3", err)
        self.assertIn("fake server: cannot import its MCP SDK", err)
        self.assertEqual(out.count("attempt"), 2, out)
        self.assertFalse(self.output.exists())

    def test_a_server_without_tools_fails(self):
        self.config("a.yaml", "alpha", tools=[])
        code, _, err = self.discover()
        self.assertEqual(code, 1)
        self.assertIn("alpha: tools/list returned no tools", err)
        self.assertFalse(self.output.exists())

    def test_a_server_that_never_answers_fails_at_the_deadline(self):
        self.config("a.yaml", "alpha", mode="silent")
        code, _, err = self.discover("--timeout", "2", "--init-retry", "0.5")
        self.assertEqual(code, 1)
        self.assertIn("alpha: no answer to initialize within 2s", err)

    def test_two_configs_with_one_name_fail(self):
        self.config("a.yaml", "alpha")
        self.config("b.yaml", "alpha")
        code, _, err = self.discover()
        self.assertEqual(code, 1)
        self.assertIn("alpha (b.yaml): the name is already used by a.yaml", err)

    def test_servers_are_launched_as_the_session_launches_them(self):
        self.config("a.yaml", "alpha", mode="launch",
                    extra_args=["${agent_workspace}/out", "${task_dir}/email_config.json", "${local_servers_paths}/x"],
                    env={"FROM_YAML": "yes", "PG_DATABASE": "toolathlon"})
        with patch.dict(os.environ):
            os.environ.pop("PGDATABASE", None)
            os.environ.pop("PG_DATABASE", None)
            code, out, err = self.discover()
        self.assertEqual(code, 0, out + err)
        launch = json.loads(json.loads(self.output.read_text())["alpha"][0]["description"])
        workspace = self.root / "ws" / "alpha"
        self.assertEqual(launch["argv"], [f"{workspace}/out", f"{self.root / 'ws' / '.task'}/email_config.json",
                                          f"{self.root / 'servers'}/x"])
        self.assertEqual(Path(launch["cwd"]).resolve(), workspace.resolve())
        # runtime_patches comes first on PYTHONPATH, as in server.py's _pg_env (its sitecustomize renames databases).
        self.assertEqual(launch["env"].pop("PYTHONPATH").split(os.pathsep)[0], discover_tools.RUNTIME_PATCHES)
        # The session database wins over a config's PG_DATABASE, as in server.py.
        self.assertEqual(launch["env"], {"PGDATABASE": "toolathlon_template", "PG_DATABASE": "toolathlon_template",
                                         "PGHOST": "localhost", "PYTHONHASHSEED": "0", "FROM_YAML": "yes"})
        self.assertFalse((self.root / "ws").exists(), "the scratch workspace is removed")

    def test_repository_configs_are_launchable_and_uniquely_named(self):
        configs, problems = discover_tools.load_configs(ROOT / "configs" / "mcp_servers")
        self.assertEqual(problems, [])
        self.assertIn("snowflake", configs)
        self.assertEqual(len(configs), len(list((ROOT / "configs" / "mcp_servers").glob("*.yaml"))))

    def test_every_server_a_task_needs_is_configured(self):
        # Discovery only knows configured servers: a task that names a server with no config would get no tools
        # for it, and the build would not notice.
        configs, _ = discover_tools.load_configs(ROOT / "configs" / "mcp_servers")
        needed: dict[str, list[str]] = {}
        for path in sorted((ROOT / "tasks" / "finalpool").glob("*/task_config.json")):
            for server in json.loads(path.read_text()).get("needed_mcp_servers", []):
                needed.setdefault(server, []).append(path.parent.name)
        self.assertIn("snowflake", needed)
        self.assertEqual({server: tasks[:3] for server, tasks in needed.items() if server not in configs}, {})


class LocalServerLockTest(unittest.TestCase):
    """The image runs `uv sync` in every Python MCP server, so each must ship the lock it was tested with.

    mcp-snowflake-server had no uv.lock, so each build resolved `mcp>=1.0.0` afresh. mcp 2 has no
    Server.list_resources(), the server died at startup, and the c1e58af catalog lost its 14 tools.
    """

    SERVERS = ROOT / "local_servers"

    def test_every_python_server_has_a_lock(self):
        projects = sorted(self.SERVERS.glob("*/pyproject.toml"))
        self.assertIn(self.SERVERS / "mcp-snowflake-server" / "pyproject.toml", projects)
        self.assertEqual([p.parent.name for p in projects if not p.with_name("uv.lock").is_file()], [])

    def test_snowflake_server_stays_on_mcp_1(self):
        server = self.SERVERS / "mcp-snowflake-server"
        locked = [p["version"] for p in tomllib.loads((server / "uv.lock").read_text())["package"] if p["name"] == "mcp"]
        self.assertEqual(len(locked), 1, locked)
        self.assertEqual(locked[0].split(".")[0], "1", f"uv.lock resolves mcp {locked[0]}")
        deps = tomllib.loads((server / "pyproject.toml").read_text())["project"]["dependencies"]
        mcp = [d.replace(" ", "") for d in deps if re.match(r"mcp\b(?![-_])", d)]
        self.assertEqual(len(mcp), 1, deps)
        self.assertIn("<2", mcp[0], "a fresh lock must not move to mcp 2 either")


if __name__ == "__main__":
    unittest.main()
