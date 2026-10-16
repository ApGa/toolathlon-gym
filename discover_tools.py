"""
Discover every configured MCP server's tool schemas at image build time.

For each YAML config in /app/configs/mcp_servers/, launch the server the way server.py launches it for a
session (same command, arguments, environment overrides and working directory), send JSON-RPC initialize
and tools/list, and record each tool's name, description and inputSchema as the server returns them.
Saves {server_name: [{name, description, inputSchema}, ...]} to /app/tool_schemas.json.

server.py builds every task's tool list from this file alone, so a server missing from it gives its tasks
no tools at run time even when the server itself starts. Discovery therefore never writes a partial
catalog: if any configured server cannot be launched, does not answer, or lists no tools, it prints why
(with the server's stderr) and exits with status 1, which fails the image build.

Every server runs in the same scratch workspace path and with PYTHONHASHSEED=0, so build-time paths and
set orders that servers put into tool descriptions are the same in every build.

With --check-entry-points it launches nothing: it checks that every configured server's entry point (the
built script or the venv and command its launch runs, read from the same configs) exists, names each
server that lacks one, and exits with status 1 if any does. The image build runs it right after building
the servers.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yaml

CONFIGS_DIR = Path("/app/configs/mcp_servers")
LOCAL_SERVERS = "/opt/local_servers"
OUTPUT_FILE = Path("/app/tool_schemas.json")
RUNTIME_PATCHES = "/app/runtime_patches"
# Not mkdtemp: the terminal server names its allowed directory in its tool description.
WORKSPACE_ROOT = Path("/tmp/toolathlon-discovery")
SERVER_TIMEOUT_S = 180.0  # launch to tools/list, per attempt; uv may still be syncing a venv
INIT_RETRY_S = 30.0  # resend initialize this often while the server stays silent
ATTEMPTS = 2
STDERR_TAIL_BYTES = 4000

# Placeholder values for token-based config vars (PG-backed servers don't need real tokens)
TOKEN_VARS = {
    "token.snowflake_account": "local_pg",
    "token.snowflake_warehouse": "local",
    "token.snowflake_user": "postgres",
    "token.snowflake_private_key_path": "",
    "token.snowflake_role": "PUBLIC",
    "token.snowflake_database": "toolathlon",
    "token.snowflake_schema": "sf",
    "token.snowflake_op_allowed_databases": "toolathlon",
    "token.google_oauth2_credentials_path": "",
    "token.google_oauth2_token_path": "",
    "token.google_sheets_folder_id": "",
    "token.emails_config_file": "",
    "token.notion_allowed_page_ids": "",
    "token.notion_integration_key_eval": "ntn-placeholder",
    "token.canvas_api_token": "placeholder",
    "token.canvas_domain": "localhost:8080",
    "token.google_client_id": "placeholder",
    "token.google_client_secret": "placeholder",
    "token.google_refresh_token": "placeholder",
    "token.woocommerce_site_url": "http://localhost:8081",
    "token.woocommerce_api_key": "placeholder",
    "token.woocommerce_api_secret": "placeholder",
}


class DiscoveryError(RuntimeError):
    """A server that could not be asked for its tools, with the reason."""


def resolve(value, workspace: str, task_dir: str, local_servers: str = LOCAL_SERVERS):
    if not isinstance(value, str):
        return value
    value = (
        value.replace("${local_servers_paths}", local_servers)
        .replace("${agent_workspace}", workspace)
        .replace("${task_dir}", task_dir)
    )
    for k, v in TOKEN_VARS.items():
        value = value.replace("${" + k + "}", v)
    return value


def load_configs(configs_dir: Path) -> tuple[dict[str, tuple[Path, dict]], list[str]]:
    """Configured servers by name (in file-name order), and what is wrong with the configs."""
    configs: dict[str, tuple[Path, dict]] = {}
    problems: list[str] = []
    for path in sorted(configs_dir.glob("*.yaml")):
        cfg = yaml.safe_load(path.read_text())
        if not cfg:
            continue  # server.py skips empty files too
        name = cfg.get("name", path.stem) if isinstance(cfg, dict) else None
        if not isinstance(name, str) or not name or "/" in name or name in {".", ".."}:
            problems.append(f"{path.name}: no usable server name ({name!r})")
            continue
        if not isinstance(cfg.get("params"), dict) or not cfg["params"].get("command"):
            problems.append(f"{name} ({path.name}): no params.command to launch")
            continue
        if name in configs:
            problems.append(f"{name} ({path.name}): the name is already used by {configs[name][0].name}")
            continue
        configs[name] = (path, cfg)
    return configs, problems


def launch_spec(cfg: dict, workspace: str, task_dir: str, database: str,
                local_servers: str = LOCAL_SERVERS) -> tuple[list[str], dict[str, str], str]:
    """argv, environment and cwd as server.py's MCPBridge.start builds them for a session."""
    params = cfg.get("params", {})
    command = resolve(params.get("command", ""), workspace, task_dir, local_servers)
    args = [resolve(a, workspace, task_dir, local_servers) for a in params.get("args", [])]
    env_vars = {k: resolve(v, workspace, task_dir, local_servers) for k, v in (params.get("env") or {}).items()}
    cwd = resolve(params.get("cwd", workspace), workspace, task_dir, local_servers)

    # For "uv run <script>" without --directory, infer cwd from the script path
    # so uv can find the project's pyproject.toml
    if command == "uv" and "run" in args and "cwd" not in params:
        run_idx = args.index("run")
        if run_idx + 1 < len(args):
            script_path = args[run_idx + 1]
            if os.path.isfile(script_path):
                cwd = os.path.dirname(script_path)

    # As ToolathlonGym._pg_env: the session database wins over a config's
    # `PG_DATABASE: "toolathlon"` (toolathlon_template at build time).
    pythonpath = os.pathsep.join(p for p in (RUNTIME_PATCHES, os.environ.get("PYTHONPATH")) if p)
    env = {
        **os.environ, **env_vars,
        "PGHOST": "localhost", "PG_HOST": "localhost",
        "PGDATABASE": database, "PG_DATABASE": database,
        "PYTHONPATH": pythonpath,
        "PYTHONHASHSEED": "0",
    }
    return [command, *args], env, cwd


def entry_points(cfg: dict, local_servers: str = LOCAL_SERVERS) -> list[str]:
    """The paths under local_servers that a server's launch runs, which the image build must produce.

    Every launch argument that is such a path: a Node server's built script, a uv project directory, a venv's
    interpreter. For `uv ... run X`, also the project's .venv/bin/python and X: the script after `run python`
    (relative to the project), or else the console script .venv/bin/X that `uv sync` installs. Paths in the
    agent workspace or the task directory are made per session, not built, so they are not entry points.
    """
    params = cfg.get("params", {})
    # Keep the workspace placeholders as they are: they never name a path under local_servers.
    argv = [str(resolve(a, "${agent_workspace}", "${task_dir}", local_servers))
            for a in [params.get("command", ""), *params.get("args", [])]]
    prefix = local_servers.rstrip("/") + "/"
    points = [a for a in argv if a.startswith(prefix)]
    if os.path.basename(argv[0]) == "uv" and "run" in argv[1:]:
        run = argv.index("run", 1)
        target = argv[run + 1] if run + 1 < len(argv) else ""
        if "--directory" in argv[1:run]:
            project = argv[argv.index("--directory", 1) + 1]
        else:
            project = os.path.dirname(target)  # `uv run <script>` runs from the script's directory (launch_spec)
        if project.startswith(prefix):
            points.append(os.path.join(project, ".venv", "bin", "python"))
            if target in ("python", "python3"):
                if run + 2 < len(argv) and not argv[run + 2].startswith("-"):
                    points.append(os.path.join(project, argv[run + 2]))
            elif target and "/" not in target:
                points.append(os.path.join(project, ".venv", "bin", target))
    return list(dict.fromkeys(points))


def missing_entry_points(cfg: dict, local_servers: str = LOCAL_SERVERS) -> list[str]:
    """What a server's launch needs and does not find: entry points that do not exist, or the command itself."""
    command = str(resolve(cfg.get("params", {}).get("command", ""), "", "", local_servers))
    missing = [p for p in entry_points(cfg, local_servers) if not os.path.exists(p)]
    if "/" not in command and shutil.which(command) is None:
        missing.insert(0, f"{command} (not on PATH)")
    elif "/" in command and os.path.exists(command) and not os.access(command, os.X_OK):
        missing.insert(0, f"{command} (not executable)")
    return missing


def check_entry_points(configs: dict[str, tuple[Path, dict]], problems: list[str], local_servers: str) -> int:
    """Print each configured server's entry points; 1, naming each server that lacks one, if any does."""
    print(f"Checking the entry points of {len(configs)} configured MCP servers...")
    for name, (path, cfg) in configs.items():
        points = entry_points(cfg, local_servers)
        missing = missing_entry_points(cfg, local_servers)
        if missing:
            problems.append(f"{name} ({path.name}): missing {', '.join(missing)}")
            print(f"  {name}: MISSING {', '.join(missing)}")
        else:
            print(f"  {name}: ok ({', '.join(points) or 'nothing under ' + local_servers + ' to check'})")
    if problems:
        # Last in the log, which is all a failed build step shows: stdout is block-buffered in a pipe.
        sys.stdout.flush()
        print(
            "\nERROR: these MCP servers cannot be launched from this image, so their tasks would get none of "
            "their tools. Build each one (the Dockerfile's server build steps) or fix its config:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"All {len(configs)} configured servers have their entry points.")
    return 0


class _Session:
    """Newline-delimited JSON-RPC over a server's stdio, with deadlines."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.lines: queue.Queue = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in iter(self.proc.stdout.readline, b""):
            self.lines.put(line)
        self.lines.put(None)

    def send(self, method: str, params: dict | None = None, *, notify: bool = False) -> int | None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        req_id = None
        if not notify:
            req_id = self.next_id
            self.next_id += 1
            msg["id"] = req_id
        if params is not None:
            msg["params"] = params
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise DiscoveryError(f"cannot write {method} to the server ({e}){self._exit_note()}") from None
        return req_id

    def wait(self, ids: set[int], deadline: float):
        """The first response to any of ids, or None when the deadline passes."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self.lines.get(timeout=remaining)
            except queue.Empty:
                return None
            if line is None:
                raise DiscoveryError(f"the server closed its stdout{self._exit_note()}")
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(msg, dict) and msg.get("id") in ids:
                return msg

    def _exit_note(self) -> str:
        try:
            code = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return ""
        return f"; it exited with status {code}"


def _request(session: _Session, method: str, params: dict, deadline: float) -> dict:
    req_id = session.send(method, params)
    msg = session.wait({req_id}, deadline)
    if msg is None:
        raise DiscoveryError(f"no answer to {method} before the deadline")
    if "result" not in msg:
        raise DiscoveryError(f"{method} failed: {msg.get('error')}")
    return msg["result"]


def _ask_tools(proc: subprocess.Popen, timeout: float, init_retry: float) -> list[dict]:
    session = _Session(proc)
    deadline = time.monotonic() + timeout
    init = {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "discover", "version": "1.0"},
    }
    # Like server.py, ask right away and ask again while the server boots.
    sent: set[int] = set()
    answer = None
    while answer is None:
        if time.monotonic() >= deadline:
            raise DiscoveryError(f"no answer to initialize within {timeout:.0f}s")
        sent.add(session.send("initialize", init))
        answer = session.wait(sent, min(deadline, time.monotonic() + init_retry))
    if "result" not in answer:
        raise DiscoveryError(f"initialize failed: {answer.get('error')}")
    session.send("notifications/initialized", notify=True)

    tools: list[dict] = []
    cursor = None
    while True:
        result = _request(session, "tools/list", {"cursor": cursor} if cursor else {}, deadline)
        page = result.get("tools")
        if not isinstance(page, list):
            raise DiscoveryError(f"tools/list returned no tool list: {json.dumps(result)[:300]}")
        tools.extend(page)
        cursor = result.get("nextCursor")
        if not cursor:
            return tools


def _stderr_tail(handle) -> str:
    handle.flush()
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - STDERR_TAIL_BYTES))
    tail = handle.read().decode(errors="replace").strip()
    return f"\n    stderr (last {STDERR_TAIL_BYTES} bytes):\n      " + tail.replace("\n", "\n      ") if tail else ""


def _stop(proc: subprocess.Popen):
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


def discover_server(name: str, cfg: dict, *, workspace_root: Path, database: str,
                    timeout: float = SERVER_TIMEOUT_S, init_retry: float = INIT_RETRY_S,
                    local_servers: str = LOCAL_SERVERS) -> list[dict]:
    """Launch one server, initialize it and return its tool schemas; DiscoveryError says why not."""
    workspace = workspace_root / name
    task_dir = workspace_root / ".task"  # no task at build time: an empty directory
    workspace.mkdir(parents=True, exist_ok=True)
    task_dir.mkdir(parents=True, exist_ok=True)
    argv, env, cwd = launch_spec(cfg, str(workspace), str(task_dir), database, local_servers)
    os.makedirs(cwd, exist_ok=True)

    with tempfile.TemporaryFile() as stderr:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                    env=env, cwd=cwd)
        except OSError as e:
            raise DiscoveryError(f"cannot launch {argv[0]!r}: {e}") from None
        try:
            tools = _ask_tools(proc, timeout, init_retry)
        except DiscoveryError as e:
            _stop(proc)
            raise DiscoveryError(f"{e}{_stderr_tail(stderr)}") from None
        _stop(proc)

    schemas = []
    for t in tools:
        if not isinstance(t, dict) or not isinstance(t.get("name"), str) or not t["name"]:
            raise DiscoveryError(f"tools/list returned a tool without a name: {json.dumps(t)[:300]}")
        schemas.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema", {}),
        })
    return schemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--configs", type=Path, default=CONFIGS_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--local-servers", default=LOCAL_SERVERS)
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--timeout", type=float, default=SERVER_TIMEOUT_S, help="seconds per attempt")
    parser.add_argument("--init-retry", type=float, default=INIT_RETRY_S)
    parser.add_argument("--attempts", type=int, default=ATTEMPTS)
    parser.add_argument("--check-entry-points", action="store_true",
                        help="launch nothing; check that every configured server's entry point exists")
    args = parser.parse_args(argv)

    configs, problems = load_configs(args.configs)
    if not configs and not problems:
        problems.append(f"no MCP server configs in {args.configs}")
    if args.check_entry_points:
        return check_entry_points(configs, problems, args.local_servers)

    print("Discovering MCP server tool schemas...")
    database = os.environ.get("PG_DATABASE") or os.environ.get("PGDATABASE") or "toolathlon_template"

    created_root = not args.workspace_root.exists()
    all_schemas: dict[str, list[dict]] = {}
    try:
        for name, (_path, cfg) in configs.items():
            print(f"  Discovering {name}...", end=" ", flush=True)
            error = ""
            for attempt in range(1, args.attempts + 1):
                try:
                    tools = discover_server(name, cfg, workspace_root=args.workspace_root, database=database,
                                            timeout=args.timeout, init_retry=args.init_retry,
                                            local_servers=args.local_servers)
                except DiscoveryError as e:
                    error = str(e)
                    print(f"attempt {attempt} failed: {error.splitlines()[0]}", end=" ", flush=True)
                    continue
                if not tools:
                    error = "tools/list returned no tools"
                    print(f"attempt {attempt}: no tools", end=" ", flush=True)
                    continue
                all_schemas[name] = tools
                print(f"OK ({len(tools)} tools)")
                break
            else:
                print("FAILED")
                problems.append(f"{name}: {error}")
    finally:
        for scratch in [*configs, ".task"]:
            shutil.rmtree(args.workspace_root / scratch, ignore_errors=True)
        if created_root:
            try:
                args.workspace_root.rmdir()
            except OSError:
                pass

    if problems:
        sys.stdout.flush()  # keep the error last when stdout and stderr share the build log
        print(
            f"\nERROR: incomplete MCP tool catalog: {len(all_schemas)} of {len(configs)} configured servers "
            f"answered. server.py offers each task only the tools in this catalog, so tasks that need a "
            f"missing server would run without its tools. {args.output} was not written.",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_name(f".{args.output.name}.partial")
    partial.write_text(json.dumps(all_schemas, indent=2))
    partial.chmod(0o644)  # server.py may run as a user other than the builder
    os.replace(partial, args.output)
    total_tools = sum(len(v) for v in all_schemas.values())
    print(f"\nSaved {len(all_schemas)} servers, {total_tools} tools to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
