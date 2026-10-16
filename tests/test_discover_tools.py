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
import subprocess
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

    def test_the_build_log_ends_with_the_error(self):
        # The image build merges discovery's stdout and stderr into one log, whose end is all a failed step shows.
        self.config("a.yaml", "alpha")
        self.config("s.yaml", "snowflake", mode="crash")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "discover_tools.py"), "--configs", str(self.configs),
             "--output", str(self.output), "--workspace-root", str(self.root / "ws"),
             "--local-servers", str(self.root / "servers"), "--timeout", "20", "--init-retry", "5", "--attempts", "1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertGreater(proc.stdout.index("ERROR: incomplete MCP tool catalog"), proc.stdout.index("FAILED"))

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


class EntryPointTest(unittest.TestCase):
    """The image build checks, before discovery, that each configured server's build left its entry point."""

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.servers = self.root / "servers"
        self.configs = self.root / "configs"
        self.configs.mkdir()

    def write_config(self, filename, name, command, args):
        (self.configs / filename).write_text(json.dumps(
            {"type": "stdio", "name": name, "params": {"command": command, "args": args}}))

    def make(self, *paths):
        for rel in paths:
            path = self.servers / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")

    def check(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = discover_tools.main(["--check-entry-points", "--configs", str(self.configs),
                                        "--local-servers", str(self.servers),
                                        "--output", str(self.root / "tool_schemas.json")])
        return code, out.getvalue(), err.getvalue()

    def test_entry_points_of_each_kind_of_launch(self):
        ls = "/opt/local_servers"
        cases = {
            "node script": ("node", ["${local_servers_paths}/a/dist/index.js", "${agent_workspace}"],
                            [f"{ls}/a/dist/index.js"]),
            "uv console script": ("uv", ["--directory", "${local_servers_paths}/b", "run", "b-server",
                                         "--out", "${agent_workspace}/x", "--config", "${task_dir}/c.json"],
                                  [f"{ls}/b", f"{ls}/b/.venv/bin/python", f"{ls}/b/.venv/bin/b-server"]),
            "uv python script": ("uv", ["--directory", "${local_servers_paths}/c", "run", "python", "server/main.py"],
                                 [f"{ls}/c", f"{ls}/c/.venv/bin/python", f"{ls}/c/server/main.py"]),
            "uv run script path": ("uv", ["run", "${local_servers_paths}/d/server.py"],
                                   [f"{ls}/d/server.py", f"{ls}/d/.venv/bin/python"]),
            "venv interpreter": ("${local_servers_paths}/e/.venv/bin/python3", ["-c", "import e"],
                                 [f"{ls}/e/.venv/bin/python3"]),
            "nothing local": ("npx", ["-y", "some-package"], []),
        }
        for kind, (command, args, expected) in cases.items():
            with self.subTest(kind):
                cfg = {"params": {"command": command, "args": args}}
                self.assertEqual(discover_tools.entry_points(cfg, ls), expected)

    def test_passes_when_every_entry_point_exists(self):
        self.make("fetch/dist/index.js", "py/.venv/bin/python", "py/.venv/bin/py-server")
        self.write_config("a.yaml", "fetch", "node", ["${local_servers_paths}/fetch/dist/index.js"])
        self.write_config("b.yaml", "py", sys.executable, [])  # an interpreter path outside local_servers
        self.write_config("c.yaml", "py2", "uv", ["--directory", "${local_servers_paths}/py", "run", "py-server"])
        with patch("shutil.which", return_value="/usr/bin/found"):
            code, out, err = self.check()
        self.assertEqual(code, 0, out + err)
        self.assertIn("All 3 configured servers have their entry points.", out)
        self.assertFalse((self.root / "tool_schemas.json").exists(), "the check writes no catalog")

    def test_names_each_server_whose_build_left_no_entry_point(self):
        self.make("ok/build/index.js", "py/.venv/bin/python")
        self.write_config("ok.yaml", "rail", "node", ["${local_servers_paths}/ok/build/index.js"])
        self.write_config("npx-fetch.yaml", "fetch", "node", ["${local_servers_paths}/mcp-npx-fetch/dist/index.js"])
        self.write_config("py.yaml", "py", "uv", ["--directory", "${local_servers_paths}/py", "run", "py-server"])
        with patch("shutil.which", return_value="/usr/bin/found"):
            code, out, err = self.check()
        self.assertEqual(code, 1, out + err)
        self.assertIn(f"fetch (npx-fetch.yaml): missing {self.servers}/mcp-npx-fetch/dist/index.js", err)
        self.assertIn(f"py (py.yaml): missing {self.servers}/py/.venv/bin/py-server", err)
        self.assertNotIn("rail", err)

    def test_the_build_log_ends_with_the_servers_that_lack_one(self):
        # A failed build step shows only its last lines, and stdout is buffered when it is a pipe.
        self.make("ok/build/index.js")
        self.write_config("npx-fetch.yaml", "fetch", sys.executable,
                          ["${local_servers_paths}/mcp-npx-fetch/dist/index.js"])
        self.write_config("ok.yaml", "rail", sys.executable, ["${local_servers_paths}/ok/build/index.js"])
        proc = subprocess.run(
            [sys.executable, str(ROOT / "discover_tools.py"), "--check-entry-points", "--configs", str(self.configs),
             "--local-servers", str(self.servers)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("fetch (npx-fetch.yaml): missing", proc.stdout.strip().splitlines()[-1])

    def test_a_command_that_is_not_installed_fails(self):
        self.make("ok/build/index.js")
        self.write_config("ok.yaml", "rail", "node", ["${local_servers_paths}/ok/build/index.js"])
        with patch("shutil.which", return_value=None):
            code, _, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("rail (ok.yaml): missing node (not on PATH)", err)

    def test_every_repository_config_has_an_entry_point_to_check(self):
        # The build's check covers exactly the configured servers, so a new config is checked without a
        # Dockerfile change; one whose launch names nothing it could check would slip past it.
        configs, problems = discover_tools.load_configs(ROOT / "configs" / "mcp_servers")
        self.assertEqual(problems, [])
        self.assertEqual([name for name, (_, cfg) in configs.items() if not discover_tools.entry_points(cfg)], [])


class LocalServerBuildTest(unittest.TestCase):
    """What the image build's `npm ci` and `npm run build` need from each Node MCP server's files."""

    SERVERS = ROOT / "local_servers"

    def node_projects(self):
        manifests = [*self.SERVERS.glob("*/package.json"), *self.SERVERS.glob("*/src/*/package.json")]
        return sorted(p.parent for p in manifests if "node_modules" not in p.parts)

    def test_tsconfig_extends_only_files_in_the_server_tree(self):
        # filesystem's tsconfig extended ../../tsconfig.json: /opt/tsconfig.json in the image, so tsc stopped with
        # TS5083 and the image kept the prebuilt dist/ from the repository.
        bad = {}
        for project in self.node_projects():
            for tsconfig in project.glob("tsconfig*.json"):
                for base in re.findall(r'"extends"\s*:\s*"([^"]+)"', tsconfig.read_text()):
                    if not base.startswith("."):
                        continue  # a package from node_modules
                    target = (tsconfig.parent / base).resolve()
                    if not target.is_file() or self.SERVERS.resolve() not in target.parents:
                        bad[str(tsconfig.relative_to(ROOT))] = base
        self.assertEqual(bad, {})

    def test_lockfiles_match_their_package_json(self):
        # `npm ci` refuses a lockfile whose root dependencies differ from package.json.
        stale = {}
        for project in self.node_projects():
            lock = project / "package-lock.json"
            if not lock.is_file():
                continue
            pkg = json.loads((project / "package.json").read_text())
            root = json.loads(lock.read_text())["packages"][""]
            for field in ("dependencies", "devDependencies", "optionalDependencies"):
                if (pkg.get(field) or {}) != (root.get(field) or {}):
                    stale.setdefault(str(lock.relative_to(ROOT)), []).append(field)
        self.assertEqual(stale, {})

    def test_lockfiles_pin_each_tarball_and_its_integrity(self):
        # Then `npm ci` downloads exactly the pinned tarballs and checks them. mcp-npx-fetch's lockfile recorded
        # neither, so each install first looked up all 337 packages in the registry's metadata.
        unpinned = {}
        for project in self.node_projects():
            lock = project / "package-lock.json"
            if not lock.is_file():
                continue
            for path, entry in json.loads(lock.read_text())["packages"].items():
                if "node_modules/" not in path or entry.get("link"):
                    continue  # the project, its workspaces and links to them
                resolved = entry.get("resolved", "")
                if not resolved or resolved.startswith("https://registry.npmjs.org/") and not entry.get("integrity"):
                    unpinned.setdefault(str(lock.relative_to(ROOT)), []).append(path)
        self.assertEqual({lock: paths[:3] for lock, paths in unpinned.items()}, {})


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
