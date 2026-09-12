"""
Toolathlon Gym — OpenReward Standard environment server.

Exposes 503 multi-tool tasks via ORS. The ORS server, PostgreSQL, and MCP
server subprocesses all run inside the same Docker container.

Tool exposure
-------------
A task's MCP tools can run into the hundreds, which blows past the 128-tool
ceiling on OpenAI-compatible inference endpoints. Instead of registering
each MCP tool individually, `list_task_tools()` returns just two
meta-tools, `get_tool_details` and `call_tool`. The full per-task catalog
(name + one-line description for every available tool) is inlined into the
system prompt so the agent sees the menu up front; it fetches a tool's
input_schema on demand via `get_tool_details` and invokes it via
`call_tool`.
"""
import asyncio
import functools
import json
import os
import random
import re
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from http_fixtures import HTTPFixture, copy_task
from task_setup import run_preprocess

import yaml
from pydantic import BaseModel, Field

from openreward.environments.environment import Environment, tool
from openreward.environments.server import Server
from openreward.environments.types import (
    ListToolsOutput,
    RunToolError,
    RunToolOutput,
    RunToolSuccess,
    TextBlock,
    ToolOutput,
    ToolSpec,
)

# ── Constants ────────────────────────────────────────────────────────────────

TASKS_ROOT = Path("/app/tasks/finalpool")
CONFIGS_DIR = Path("/app/configs/mcp_servers")
LOCAL_SERVERS = "/opt/local_servers"
TOOL_SCHEMAS_FILE = Path("/app/tool_schemas.json")

CATALOG_DESC_MAX_CHARS = 160
MCP_OUTPUT_MAX_CHARS = int(os.getenv("OPENREWARD_MCP_OUTPUT_MAX_CHARS", "50000"))
MCP_OUTPUT_MAX_JSON_STRING_CHARS = int(os.getenv("OPENREWARD_MCP_OUTPUT_MAX_JSON_STRING_CHARS", "2000"))
MCP_SUBPROCESS_STREAM_LIMIT = int(os.getenv("OPENREWARD_MCP_SUBPROCESS_STREAM_LIMIT", str(16 * 1024 * 1024)))
REWARD_MODE_ENV = "OPENREWARD_REWARD_MODE"
TOOL_ROUTING_SCHEMA_KEY = "x-openhands-tool-routing"
TOOL_ERROR_META_KEY = "openhands.dev/tool-error"


def _with_tool_routing(
    spec: ToolSpec,
    *,
    capabilities: tuple[str, ...],
    invocation: dict[str, Any],
) -> ToolSpec:
    """Attach routing metadata through an ignorable JSON-Schema extension.

    OpenReward's current ``ToolSpec`` has no arbitrary metadata field.  A
    namespaced root schema extension survives the OpenReward API and remains
    harmless to clients that do not understand orchestration routing.
    """

    schema = dict(spec.input_schema or {"type": "object", "properties": {}})
    schema[TOOL_ROUTING_SCHEMA_KEY] = {
        "version": 1,
        "execution_domain": "task",
        "capabilities": list(capabilities),
        "invocation": invocation,
    }
    return spec.model_copy(update={"input_schema": schema})


def _tool_error_output(message: str, *, kind: str) -> ToolOutput:
    """Return a recoverable error that tool-aware clients can identify.

    OpenReward ``ToolOutput`` does not yet have an error discriminator.  Keep
    the session usable for a corrective call and declare the error through a
    namespaced metadata marker that the Platoon OpenReward bridge translates
    to MCP ``isError``.
    """

    return ToolOutput(
        blocks=[TextBlock(text=message)],
        metadata={
            TOOL_ERROR_META_KEY: {
                "kind": kind,
                "message": message,
            }
        },
    )

# ── Load pre-discovered tool schemas ─────────────────────────────────────────

ALL_TOOL_SCHEMAS: dict[str, list[dict]] = {}
if TOOL_SCHEMAS_FILE.exists():
    ALL_TOOL_SCHEMAS = json.loads(TOOL_SCHEMAS_FILE.read_text())


def _json_path(parent: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    key_str = str(key)
    if key_str.isidentifier():
        return f"{parent}.{key_str}"
    return f"{parent}[{key_str!r}]"


def _sanitize_json_value(
    value: Any,
    truncations: list[dict[str, Any]],
    path: str = "$",
) -> Any:
    """Keep MCP JSON results readable without giant unsplittable string lines."""
    if isinstance(value, str):
        if len(value) > MCP_OUTPUT_MAX_JSON_STRING_CHARS:
            omitted = len(value) - MCP_OUTPUT_MAX_JSON_STRING_CHARS
            truncations.append({"path": path, "omitted_chars": omitted})
            return (
                value[:MCP_OUTPUT_MAX_JSON_STRING_CHARS]
                + f"... [truncated {omitted} chars at {path}]"
            )
        return value
    if isinstance(value, list):
        return [
            _sanitize_json_value(item, truncations, _json_path(path, index))
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _sanitize_json_value(item, truncations, _json_path(path, key))
            for key, item in value.items()
        }
    return value


def _safe_mcp_output(text: str) -> str:
    """Bound MCP tool output and make any truncation explicit.

    Some MCP servers return large JSON blobs with very long string fields (for
    example Canvas HTML bodies). Prefer preserving valid JSON by truncating
    long string values and adding an explicit notice with the affected paths.
    """
    notices: list[str] = []
    try:
        parsed = json.loads(text)
        truncations: list[dict[str, Any]] = []
        sanitized = _sanitize_json_value(parsed, truncations)
        if truncations:
            notice = {
                "message": (
                    "Large MCP output was shortened before returning it to "
                    "the agent to avoid downstream chunking errors. Use a "
                    "more specific Canvas query/get tool if omitted content "
                    "is needed."
                ),
                "max_json_string_chars": MCP_OUTPUT_MAX_JSON_STRING_CHARS,
                "truncated_field_count": len(truncations),
                "truncated_fields": truncations[:50],
            }
            if isinstance(sanitized, dict):
                sanitized = {"__toolathlon_output_notice": notice, **sanitized}
            else:
                sanitized = {
                    "__toolathlon_output_notice": notice,
                    "data": sanitized,
                }
        text = json.dumps(sanitized, indent=2)
    except (TypeError, json.JSONDecodeError):
        pass

    if len(text) > MCP_OUTPUT_MAX_CHARS:
        omitted = len(text) - MCP_OUTPUT_MAX_CHARS
        notices.append(
            f"Large MCP output exceeded {MCP_OUTPUT_MAX_CHARS} characters; "
            f"{omitted} trailing characters were omitted."
        )
        text = text[:MCP_OUTPUT_MAX_CHARS] + f"\n... (output truncated, {omitted} chars omitted)"

    if notices:
        return "Toolathlon output notice: " + " ".join(notices) + "\n" + text
    return text


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _score_from_counts(passed: Any, total: Any = None, failed: Any = None) -> float | None:
    passed_num = _as_number(passed)
    total_num = _as_number(total)
    failed_num = _as_number(failed)
    if total_num is None and failed_num is not None and passed_num is not None:
        total_num = passed_num + failed_num
    if passed_num is None or total_num is None or total_num <= 0:
        return None
    return max(0.0, min(1.0, passed_num / total_num))


def _score_from_result_json(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None

    for passed_key, total_key in (
        ("total_passed", "total_checks"),
        ("passed", "total"),
        ("pass", "total"),
    ):
        if passed_key in value and total_key in value:
            score = _score_from_counts(value.get(passed_key), value.get(total_key))
            if score is not None:
                return score

    for passed_key, failed_key in (
        ("passed", "failed"),
        ("pass", "fail"),
        ("total_passed", "total_failed"),
    ):
        if passed_key in value and failed_key in value:
            score = _score_from_counts(value.get(passed_key), failed=value.get(failed_key))
            if score is not None:
                return score

    for percent_key in ("accuracy", "percentage", "percent"):
        if percent_key in value:
            percent = _as_number(value.get(percent_key))
            if percent is not None:
                return max(0.0, min(1.0, percent / 100.0))

    for score_key in ("reward", "score"):
        if score_key in value:
            score = _as_number(value.get(score_key))
            if score is not None and 0.0 <= score <= 1.0:
                return score

    for item in value.values():
        if isinstance(item, dict):
            score = _score_from_result_json(item)
            if score is not None:
                return score
    return None


def _score_from_eval_output(output: str) -> float | None:
    patterns = (
        r"Overall:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s+checks\s+passed",
        r"Results?:\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s+passed",
        r"Passed:\s*(\d+(?:\.\d+)?)\s*,?\s*Failed:\s*(\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if not match:
            continue
        first = float(match.group(1))
        second = float(match.group(2))
        if "failed" in pattern.lower():
            return _score_from_counts(first, failed=second)
        return _score_from_counts(first, second)
    return None


def _partial_reward_from_grader(res_log: Path, output: str) -> float | None:
    if res_log.exists():
        try:
            score = _score_from_result_json(json.loads(res_log.read_text()))
            if score is not None:
                return score
        except (OSError, json.JSONDecodeError):
            pass
    return _score_from_eval_output(output)


def _reward_for_evaluation(binary_reward: float, res_log: Path, output: str) -> float:
    mode = os.getenv(REWARD_MODE_ENV, "binary").strip().lower()
    if mode not in {"partial", "partials", "fractional"}:
        return binary_reward

    partial_reward = _partial_reward_from_grader(res_log, output)
    if partial_reward is None:
        return binary_reward
    return partial_reward


# ── Server configuration ─────────────────────────────────────────────────────

def _get_server_port() -> int:
    raw_port = os.getenv("OPENREWARD_PORT") or os.getenv("PORT") or "8080"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(f"Invalid server port: {raw_port!r}") from exc

    if not 1 <= port <= 65535:
        raise ValueError(f"Server port must be between 1 and 65535: {port}")

    return port


def _now() -> datetime:
    """Wall-clock now, unless OPENREWARD_FROZEN_DATE pins the calendar date.

    Many tasks (and the PG-backed 12306 server's `checkDate`) compare task
    dates against "today". The benchmark was authored with a fixed reference
    date, so once real wall-clock drifts past it those tasks become
    unsolvable. Setting OPENREWARD_FROZEN_DATE=YYYY-MM-DD freezes the calendar
    date the env reports (time-of-day still tracks the wall clock) so the
    benchmark stays reproducible. The same env var is read by the 12306 MCP
    server (it is inherited by every subprocess).
    """
    frozen = os.getenv("OPENREWARD_FROZEN_DATE")
    if frozen:
        try:
            d = datetime.strptime(frozen.strip()[:10], "%Y-%m-%d").date()
            return datetime.combine(d, datetime.now().time())
        except ValueError:
            pass
    return datetime.now()


# psql/Postgres errors that are transient under high concurrency (connection
# exhaustion, startup races, template-in-use) and worth retrying with backoff
# instead of failing the whole session setup.
_TRANSIENT_PG_ERRORS = (
    "too many clients",
    "remaining connection slots",
    "could not connect",
    "connection refused",
    "the database system is starting up",
    "the database system is shutting down",
    "is being accessed by other users",
    "could not obtain lock",
    "deadlock detected",
)


# ── Template resolution (from original tool_servers.py) ──────────────────────

def _resolve(value: str, workspace: str) -> str:
    if not isinstance(value, str):
        return value
    return (
        value
        .replace("${local_servers_paths}", LOCAL_SERVERS)
        .replace("${agent_workspace}", workspace)
    )


# ── MCP Bridge — manages MCP server subprocesses per session ─────────────────

def _make_request(method: str, params: dict | None = None, req_id: int = 1) -> bytes:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode()


class _MCPProcess:
    """A single MCP server subprocess with JSON-RPC communication."""

    def __init__(self, name: str, proc: asyncio.subprocess.Process):
        self.name = name
        self.proc = proc
        self._req_id = 10

    async def send_recv(self, method: str, params: dict | None = None, timeout: float = 30) -> dict | None:
        self._req_id += 1
        req_id = self._req_id
        req = _make_request(method, params, req_id)
        self.proc.stdin.write(req)
        await self.proc.stdin.drain()

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                line = await asyncio.wait_for(
                    self.proc.stdout.readline(),
                    timeout=max(0.1, deadline - asyncio.get_event_loop().time()),
                )
            except asyncio.TimeoutError:
                break
            if not line:
                await asyncio.sleep(0.05)
                continue
            try:
                resp = json.loads(line.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if resp.get("id") == req_id:
                return resp
        return None

    async def initialize(self) -> bool:
        resp = await self.send_recv("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "toolathlon-ors", "version": "1.0"},
        }, timeout=15)
        if resp and "result" in resp:
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            self.proc.stdin.write(notif.encode())
            await self.proc.stdin.drain()
            return True
        return False

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        resp = await self.send_recv("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout=120)
        if resp is None:
            raise TimeoutError("MCP tool call timed out after 120 seconds")
        if "error" in resp:
            error = resp["error"]
            detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise RuntimeError(f"MCP tool call failed: {detail}")
        result = resp.get("result", {})
        # Extract content from MCP result
        content_parts = result.get("content", [])
        texts = []
        for part in content_parts:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif isinstance(part, dict):
                texts.append(json.dumps(part))
            else:
                texts.append(str(part))
        output = "\n".join(texts) if texts else json.dumps(result)
        if result.get("isError") is True:
            raise RuntimeError(output or "MCP tool reported an error")
        return output

    async def close(self):
        try:
            self.proc.terminate()
            await asyncio.wait_for(self.proc.wait(), timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class MCPBridge:
    """Manages MCP server subprocesses for a single session."""

    def __init__(self, needed_servers: list[str], workspace_dir: str, pg_env: dict[str, str]):
        self.needed_servers = needed_servers
        self.workspace_dir = workspace_dir
        # Env overrides (PGHOST, PGDATABASE, …) applied AFTER any YAML-set env
        # so e.g. `PG_DATABASE: "toolathlon"` in 12306.yaml gets overridden by
        # the per-session DB name.
        self.pg_env = pg_env
        self._processes: dict[str, _MCPProcess] = {}
        # bare tool name → list of servers offering it. Multi-valued because
        # different MCP servers can advertise tools with the same name; callers
        # should pass `server` to disambiguate when needed.
        self._tool_servers: dict[str, list[str]] = defaultdict(list)
        for server in needed_servers:
            for tool_info in ALL_TOOL_SCHEMAS.get(server, []):
                self._tool_servers[tool_info["name"]].append(server)

    async def start(self):
        """Launch and initialize all needed MCP servers."""
        for yaml_path in sorted(CONFIGS_DIR.glob("*.yaml")):
            with open(yaml_path) as f:
                cfg = yaml.safe_load(f)
            if not cfg:
                continue
            name = cfg.get("name", yaml_path.stem)
            if name not in self.needed_servers:
                continue

            params = cfg.get("params", {})
            command = _resolve(params.get("command", ""), self.workspace_dir)
            args = [_resolve(a, self.workspace_dir) for a in params.get("args", [])]
            env_vars = {k: _resolve(v, self.workspace_dir) for k, v in params.get("env", {}).items()}
            cwd = _resolve(params.get("cwd", self.workspace_dir), self.workspace_dir)

            # For "uv run <script>" without --directory, infer cwd from script path
            if command == "uv" and "run" in args and "cwd" not in params:
                run_idx = args.index("run")
                if run_idx + 1 < len(args):
                    script_path = args[run_idx + 1]
                    if os.path.isfile(script_path):
                        cwd = os.path.dirname(script_path)

            # Apply pg_env AFTER YAML env so 12306/youtube/youtube_transcript
            # (which hardcode `PG_DATABASE: "toolathlon"`) get rewired to the
            # per-session DB.
            full_env = {**os.environ, **env_vars, **self.pg_env}
            os.makedirs(cwd, exist_ok=True)

            try:
                proc = await asyncio.create_subprocess_exec(
                    command, *args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=full_env,
                    cwd=cwd,
                    limit=MCP_SUBPROCESS_STREAM_LIMIT,
                )
                mcp_proc = _MCPProcess(name, proc)

                # Readiness-poll instead of a fixed sleep. initialize() blocks
                # up to its own timeout for the server's response, so under
                # load a slow (node/uv) boot is tolerated without paying a
                # flat per-server delay on the fast path. Retry a few times in
                # case the process is still wiring up stdio.
                ok = False
                for attempt in range(4):
                    if proc.returncode is not None:
                        break  # process already exited; don't keep polling
                    try:
                        if await mcp_proc.initialize():
                            ok = True
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.5 * (attempt + 1))

                if ok:
                    self._processes[name] = mcp_proc
                else:
                    detail = ""
                    if proc.returncode is not None and proc.stderr is not None:
                        try:
                            err = await asyncio.wait_for(proc.stderr.read(2000), timeout=1)
                            detail = f" (rc={proc.returncode}): {err.decode(errors='replace').strip()[:300]}"
                        except Exception:
                            detail = f" (rc={proc.returncode})"
                    print(f"[MCPBridge] WARNING: Failed to initialize {name}{detail}", flush=True)
                    await mcp_proc.close()
            except Exception as e:
                print(f"[MCPBridge] WARNING: Failed to launch {name}: {e}", flush=True)

    async def call_tool(self, tool_name: str, arguments: dict, *, server: str | None = None) -> str:
        candidates = self._tool_servers.get(tool_name) or []
        if not candidates:
            raise ValueError(f"Unknown tool '{tool_name}'")
        if server is not None:
            if server not in candidates:
                raise ValueError(
                    f"Tool '{tool_name}' is not provided by server '{server}'"
                )
            chosen = server
        else:
            chosen = candidates[0]  # ambiguous: first registered wins
        proc = self._processes.get(chosen)
        if not proc:
            raise RuntimeError(f"Server '{chosen}' is not running")
        return await proc.call_tool(tool_name, arguments)

    async def close(self):
        for proc in self._processes.values():
            await proc.close()
        self._processes.clear()


# ── Input models ─────────────────────────────────────────────────────────────

class PythonExecuteInput(BaseModel):
    code: str = Field(description="Python code to execute")


class GetToolDetailsInput(BaseModel):
    name: str = Field(
        description=(
            "Fully qualified tool name as listed in the system-prompt catalog, "
            "formatted as `server.tool_name` (e.g. `notion.search`)."
        ),
    )


class CallToolInput(BaseModel):
    name: str = Field(
        description=(
            "Fully qualified tool name as listed in the system-prompt catalog, "
            "formatted as `server.tool_name` (e.g. `notion.search`). The prefix "
            "identifies which MCP server provides the tool."
        ),
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON object of arguments matching the tool's input_schema.",
    )


# ── Toolathlon Gym Environment ───────────────────────────────────────────────

class ToolathlonGym(Environment):

    @classmethod
    @functools.cache
    def list_tools(cls) -> ListToolsOutput:
        """Advertise routing metadata for the environment's direct tools."""

        tools: list[ToolSpec] = []
        for spec in super().list_tools().tools:
            if spec.name == "python_execute":
                spec = _with_tool_routing(
                    spec,
                    capabilities=(
                        "python.execute",
                        "filesystem.read",
                        "filesystem.write",
                        "system.execute",
                    ),
                    invocation={"kind": "direct"},
                )
            elif spec.name == "claim_done":
                spec = _with_tool_routing(
                    spec,
                    capabilities=("task.submit",),
                    invocation={"kind": "direct"},
                )
            tools.append(spec)
        return ListToolsOutput(tools=tools)

    def __init__(self, task_spec: dict = {}, secrets: dict[str, str] = {}):
        super().__init__(task_spec, secrets)
        self.task_name: str = task_spec.get("task_name", "")
        self.task_dir = TASKS_ROOT / self.task_name
        self._source_task_dir = self.task_dir
        self._session_dir: Path | None = None
        self._http_fixture: HTTPFixture | None = None
        self.workspace_dir = Path(f"/tmp/workspaces/{uuid4()}")
        # Per-session Postgres DB cloned from `toolathlon_template` so tasks
        # can never see each other's mutations, even when sessions share a pod.
        self._db_name = f"s_{uuid4().hex[:16]}"
        self._task_config: dict = {}
        _launch_dt = _now()
        self._launch_time = _launch_dt.strftime("%Y-%m-%d %H:%M:%S")
        self._launch_time_display = _launch_dt.strftime("%Y-%m-%d %H:%M:%S %A")
        self._mcp_bridge: MCPBridge | None = None
        # Build set of MCP tool names for routing in _call_tool. Keys are the
        # bare (unprefixed) tool names that MCPBridge dispatches against.
        self._mcp_tool_names: set[str] = set()
        # Per-task catalog of tool entries, populated in setup(). Each entry:
        # {name, bare_name, server, description, input_schema}. `name` is the
        # canonical `server.bare_name` form used by call_tool / get_tool_details.
        self._task_tool_index: list[dict] = []
        self._task_tool_by_name: dict[str, dict] = {}

    def _pg_env(self) -> dict[str, str]:
        """Env additions that point any subprocess at this session's DB."""
        pythonpath_parts = ["/app/runtime_patches"]
        existing_pythonpath = os.environ.get("PYTHONPATH")
        if existing_pythonpath:
            pythonpath_parts.append(existing_pythonpath)
        return {
            "PGHOST": "localhost",
            "PG_HOST": "localhost",
            "PGDATABASE": self._db_name,
            "PG_DATABASE": self._db_name,
            "PYTHONPATH": os.pathsep.join(pythonpath_parts),
        }

    async def _psql(self, sql: str, *, dbname: str = "postgres", retries: int = 10) -> None:
        # Retry transient failures (connection exhaustion, startup races,
        # template-in-use) with exponential backoff + jitter. Under 128-wide
        # concurrency a momentary "too many clients" on CREATE DATABASE must
        # not kill the whole session — it just needs to wait its turn.
        last_err = ""
        for attempt in range(retries):
            proc = await asyncio.create_subprocess_exec(
                "psql", "-U", "eigent", "-d", dbname, "-v", "ON_ERROR_STOP=1", "-c", sql,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PGHOST": "localhost"},
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                return
            last_err = stderr.decode()[:500]
            is_transient = any(s in last_err.lower() for s in _TRANSIENT_PG_ERRORS)
            if not is_transient or attempt == retries - 1:
                raise RuntimeError(f"psql failed ({proc.returncode}): {last_err}")
            delay = min(0.5 * (2 ** attempt), 8.0) + random.uniform(0, 0.5)
            await asyncio.sleep(delay)
        raise RuntimeError(f"psql failed after {retries} attempts: {last_err}")

    async def setup(self):
        # Clone the seeded template into a fresh per-session DB. Done first so
        # preprocess and the MCP servers all talk to a clean copy of the world.
        await self._psql(
            f'CREATE DATABASE "{self._db_name}" TEMPLATE toolathlon_template;'
        )
        try:
            await self._setup_after_db()
        except BaseException:
            # The framework only calls teardown() once the env is registered
            # in active_envs, which happens AFTER setup() returns. If we fail
            # mid-setup, release all resources ourselves so we don't leak.
            try:
                await self.teardown()
            except Exception as exc:
                print(f"[setup] Cleanup failed task={self.task_name} session={self._db_name}: {exc}", flush=True)
            raise

    async def _setup_after_db(self):
        # Preprocessors and graders sometimes write next to __file__. Keep the
        # image immutable and make those paths private and user-owned too.
        if not (self._source_task_dir / "task_config.json").is_file():
            raise ValueError(f"Unknown task: {self.task_name!r}")
        self._session_dir = Path(tempfile.mkdtemp(prefix=f"toolathlon-{self._db_name}-"))
        self.task_dir = self._session_dir / "task"
        copy_task(self._source_task_dir, self.task_dir)

        # Load task config
        config_path = self.task_dir / "task_config.json"
        if config_path.exists():
            self._task_config = json.loads(config_path.read_text())

        needed_servers = self._task_config.get("needed_mcp_servers", [])

        if spec := self._task_config.get("http_fixture"):
            self._http_fixture = HTTPFixture(self.task_dir, self._session_dir, spec)
            self._http_fixture.start()
            # Prompts, initial text inputs, database seed URLs in preprocessing,
            # and grader expectations all use the episode's actual endpoint.
            self._http_fixture.rewrite_tree(self.task_dir)

        # Build the per-task tool index used by both the system-prompt catalog
        # and the meta-tools. Every tool is exposed with a `server.bare`
        # prefix so the source MCP server is always explicit and dispatch is
        # unambiguous even when bare names collide.
        for server in needed_servers:
            for tool_info in ALL_TOOL_SCHEMAS.get(server, []):
                bare = tool_info["name"]
                entry = {
                    "name": f"{server}.{bare}",
                    "bare_name": bare,
                    "server": server,
                    "description": tool_info.get("description", "") or "",
                    "input_schema": tool_info.get("inputSchema") or {},
                }
                self._task_tool_index.append(entry)
                self._task_tool_by_name[entry["name"]] = entry
                self._mcp_tool_names.add(bare)

        # Create workspace
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # Copy initial workspace files
        initial_ws = self.task_dir / "initial_workspace"
        if initial_ws.exists():
            for item in initial_ws.iterdir():
                dest = self.workspace_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

        # Create special directories
        for subdir in ["arxiv_local_storage", "memory", ".playwright_output"]:
            (self.workspace_dir / subdir).mkdir(exist_ok=True)

        await run_preprocess(
            self.task_dir / "preprocess" / "main.py", self.workspace_dir,
            self._session_dir / "preprocess.log", task=self.task_name,
            session=self._db_name, launch_time=self._launch_time,
            env={**os.environ, **self._pg_env()},
        )
        if self._http_fixture is not None:
            self._http_fixture.rewrite_tree(self.workspace_dir)
            self._http_fixture.check_ready()

        # Start MCP servers
        if needed_servers:
            self._mcp_bridge = MCPBridge(needed_servers, str(self.workspace_dir), self._pg_env())
            await self._mcp_bridge.start()

    def _fixture_urls(self, value: Any) -> Any:
        fixture = getattr(self, "_http_fixture", None)
        return fixture.rewrite(value) if fixture is not None else value

    def get_prompt(self) -> Sequence[TextBlock]:
        parts = []

        # Read agent system prompt
        sys_prompt_path = self.task_dir / "docs" / "agent_system_prompt.md"
        if sys_prompt_path.exists():
            sys_prompt = sys_prompt_path.read_text()
            sys_prompt = sys_prompt.replace(
                "!!<<<<||||workspace_dir||||>>>>!!", str(self.workspace_dir)
            )
            sys_prompt = sys_prompt.replace(
                "!!<<<<||||time||||>>>>!!", self._launch_time_display
            )
            parts.append(sys_prompt)

        # The agent only ever sees the get_tool_details / call_tool meta-tools,
        # so we inline the per-task tool catalog up front — otherwise it has
        # no way to know what MCP tools exist.
        parts.append(self._tool_catalog_block())

        # Read task description
        task_md_path = self.task_dir / "docs" / "task.md"
        if task_md_path.exists():
            parts.append(task_md_path.read_text())

        # Append completion instruction
        parts.append("\nWhen you have completed the task, call the `claim_done` tool.")

        return [TextBlock(text="\n\n".join(parts))]

    @staticmethod
    def _short_desc(text: str, max_chars: int = CATALOG_DESC_MAX_CHARS) -> str:
        """Collapse whitespace and truncate so each catalog entry fits one line."""
        flat = " ".join((text or "").split())
        if len(flat) <= max_chars:
            return flat
        return flat[: max_chars - 1].rstrip() + "…"

    def _tool_catalog_block(self) -> str:
        by_server: dict[str, list[dict]] = defaultdict(list)
        for entry in self._task_tool_index:
            by_server[entry["server"]].append(entry)

        lines: list[str] = [
            "## Tools",
            "",
            "You have **two layers** of tools. Treat them differently.",
            "",
            "### 1. Direct tools — call by name like any normal tool",
            "",
            "Your top-level tool list contains a small set of tools registered "
            "directly by the environment, including `get_tool_details`, "
            "`call_tool`, `python_execute`, and the task-completion tool "
            "(check your tool list for its exact registered name, e.g. "
            "`claim_done`). Invoke any of these the way you invoke any "
            "function tool — by its own name, with its own arguments.",
            "",
            "**Do NOT pass these names to `call_tool`.** `call_tool` is "
            "exclusively for the MCP catalog below. For example, to finish a "
            "task call the completion tool directly with no arguments — "
            "never `call_tool(name=\"claim_done\", ...)` or "
            "`call_tool(name=\"functions.claim_done\", ...)`.",
            "",
            "The two meta-tools you'll use to reach the catalog:",
            "",
            "- `get_tool_details(name)` — return the full `input_schema` "
            "(and untruncated description) for one MCP tool. `name` is the "
            "dotted `server.tool_name` form from the catalog below.",
            "- `call_tool(name, arguments)` — invoke one MCP tool from the "
            "catalog below. `name` is again the dotted `server.tool_name`; "
            "`arguments` must conform to that tool's `input_schema`.",
            "",
            f"### 2. MCP tool catalog — {len(self._task_tool_index)} tools "
            f"across {len(by_server)} servers",
            "",
            "These tools are **not** in your top-level tool list; the only "
            "way to invoke them is via `call_tool`. Each is shown below as "
            "`server.tool_name — short description`. Workflow: scan the "
            "catalog → call `get_tool_details` if you need the full "
            "input_schema → invoke via `call_tool`.",
            "",
        ]
        for server in sorted(by_server):
            lines.append(f"#### {server}")
            for entry in sorted(by_server[server], key=lambda e: e["name"]):
                desc = self._short_desc(entry["description"])
                if desc:
                    lines.append(f"- `{entry['name']}` — {desc}")
                else:
                    lines.append(f"- `{entry['name']}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def list_task_tools(self) -> ListToolsOutput:
        return ListToolsOutput(tools=[
            _with_tool_routing(
                ToolSpec(
                    name="get_tool_details",
                    description=(
                        "Return the full input_schema and description for one MCP "
                        "tool, looked up by its `server.tool_name` identifier "
                        "(as listed in the system-prompt catalog)."
                    ),
                    input_schema=GetToolDetailsInput.model_json_schema(),
                ),
                capabilities=("tool.discover",),
                invocation={"kind": "direct"},
            ),
            _with_tool_routing(
                ToolSpec(
                    name="call_tool",
                    description=(
                        "Invoke an MCP tool. Pass the `server.tool_name` from the "
                        "system-prompt catalog and an `arguments` object matching "
                        "that tool's input_schema (fetch via get_tool_details)."
                    ),
                    input_schema=CallToolInput.model_json_schema(),
                ),
                capabilities=("tool.dispatch",),
                invocation={
                    "kind": "dispatcher",
                    "name_argument": "name",
                    "arguments_argument": "arguments",
                    "discovery_tool": "get_tool_details",
                    "targets": [],
                },
            ),
        ])

    async def _call_tool(self, name: str, input: dict) -> RunToolOutput:
        # Handle MCP tool names that arrive directly (some inference clients
        # may flatten the catalog). Prefixed `server.tool` form disambiguates
        # collisions; without a prefix we let the bridge pick.
        explicit_server: str | None = None
        mcp_tool_name = name
        if "." in name:
            head, tail = name.split(".", 1)
            mcp_tool_name = tail
            explicit_server = head

        if mcp_tool_name in self._mcp_tool_names and self._mcp_bridge:
            try:
                result_text = await self._mcp_bridge.call_tool(
                    mcp_tool_name, self._fixture_urls(input), server=explicit_server
                )
                result_text = _safe_mcp_output(self._fixture_urls(result_text))
                return RunToolOutput(RunToolSuccess(
                    output=ToolOutput(blocks=[TextBlock(text=result_text)])
                ))
            except Exception as e:
                return RunToolOutput(RunToolError(error=f"MCP tool error: {e}"))

        # Fall through to built-in tools (claim_done, python_execute,
        # get_tool_details, call_tool).
        return await super()._call_tool(name, input)

    @tool(shared=False)
    async def get_tool_details(self, params: GetToolDetailsInput) -> ToolOutput:
        """Return the full schema and description for one MCP tool."""
        entry = self._task_tool_by_name.get(params.name)
        if entry is None:
            raise ValueError(
                f"Unknown catalog tool '{params.name}'. Pass the exact "
                "`server.tool_name` shown in the system-prompt catalog."
            )
        payload = {
            "name": entry["name"],
            "server": entry["server"],
            "description": entry["description"],
            "input_schema": entry["input_schema"],
        }
        return ToolOutput(blocks=[TextBlock(text=json.dumps(payload, indent=2))])

    @tool(shared=False)
    async def call_tool(self, params: CallToolInput) -> ToolOutput:
        """Invoke an MCP tool by name, dispatching through the running bridge."""
        name = params.name
        entry = self._task_tool_by_name.get(name)
        if entry is None:
            raise ValueError(
                f"Unknown catalog tool '{name}'. Pass the exact "
                "`server.tool_name` shown in the system-prompt catalog."
            )
        if self._mcp_bridge is None:
            raise RuntimeError("MCP bridge is not running for this task")

        try:
            result_text = await self._mcp_bridge.call_tool(
                entry["bare_name"], self._fixture_urls(dict(params.arguments)), server=entry["server"]
            )
        except Exception as exc:
            return _tool_error_output(
                f"MCP catalog tool '{name}' failed: {exc}",
                kind="mcp_tool_error",
            )

        result_text = _safe_mcp_output(self._fixture_urls(result_text))
        return ToolOutput(blocks=[TextBlock(text=result_text)])

    @tool
    async def claim_done(self) -> ToolOutput:
        """Signal that the task is complete and trigger evaluation."""
        eval_script = self.task_dir / "evaluation" / "main.py"
        if not eval_script.exists():
            return ToolOutput(
                blocks=[TextBlock(text="No evaluation script found.")],
                reward=0.0,
                finished=True,
            )

        res_log = self.workspace_dir / "eval_result.json"
        groundtruth = self.task_dir / "groundtruth_workspace"

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(eval_script),
                "--agent_workspace", str(self.workspace_dir),
                "--groundtruth_workspace", str(groundtruth),
                "--launch_time", self._launch_time,
                "--res_log_file", str(res_log),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.task_dir / "evaluation"),
                env={**os.environ, **self._pg_env()},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            output = stdout.decode() + stderr.decode()

            if proc.returncode == 0:
                reward = _reward_for_evaluation(1.0, res_log, output)
                return ToolOutput(
                    blocks=[TextBlock(text=f"PASS\n{output}")],
                    reward=reward,
                    finished=True,
                )
            else:
                reward = _reward_for_evaluation(0.0, res_log, output)
                return ToolOutput(
                    blocks=[TextBlock(text=f"FAIL\n{output}")],
                    reward=reward,
                    finished=True,
                )
        except asyncio.TimeoutError:
            return ToolOutput(
                blocks=[TextBlock(text="Evaluation timed out after 120s")],
                reward=0.0,
                finished=True,
            )
        except Exception as e:
            return ToolOutput(
                blocks=[TextBlock(text=f"Evaluation error: {e}")],
                reward=0.0,
                finished=True,
            )

    @tool
    async def python_execute(self, params: PythonExecuteInput) -> ToolOutput:
        """Execute Python code in the workspace and return stdout/stderr."""
        code_file = self.workspace_dir / f"_exec_{uuid4().hex[:8]}.py"
        code_file.write_text(self._fixture_urls(params.code))

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(code_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workspace_dir),
                env={**os.environ, **self._pg_env()},
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = self._fixture_urls(stdout.decode())
            errors = self._fixture_urls(stderr.decode())
            result = ""
            if output:
                result += f"stdout:\n{output}\n"
            if errors:
                result += f"stderr:\n{errors}\n"
            if not result:
                result = "(no output)"
            if proc.returncode != 0:
                return _tool_error_output(
                    f"Python exited with status {proc.returncode}.\n{result}",
                    kind="nonzero_exit",
                )
            return ToolOutput(blocks=[TextBlock(text=result)])
        except asyncio.TimeoutError:
            return _tool_error_output(
                "Python execution timed out after 60 seconds.",
                kind="timeout",
            )
        except Exception as e:
            return _tool_error_output(
                f"Python execution failed: {e}",
                kind="execution_error",
            )
        finally:
            code_file.unlink(missing_ok=True)

    async def teardown(self):
        failures = []
        cancelled = None
        if self._mcp_bridge:
            try:
                await self._mcp_bridge.close()
            except asyncio.CancelledError as exc:
                # Rollout cancellation must still release the episode's
                # fixture, files, and database before it propagates.
                cancelled = exc
            except Exception as exc:
                failures.append(f"MCP cleanup: {exc}")
        if self._http_fixture is not None:
            try:
                self._http_fixture.close()
            except Exception as exc:
                failures.append(f"HTTP fixture cleanup: {exc}")
        shutil.rmtree(self.workspace_dir, ignore_errors=True)
        if self._session_dir is not None:
            shutil.rmtree(self._session_dir, ignore_errors=True)
        # Drop the per-session DB. Best-effort: a leak just means the next
        # entrypoint.sh sweep will collect it.
        try:
            await self._psql(
                f'DROP DATABASE IF EXISTS "{self._db_name}" WITH (FORCE);'
            )
        except Exception as e:
            print(f"[teardown] DROP DATABASE {self._db_name} failed: {e}", flush=True)
        if cancelled is not None:
            raise cancelled
        if failures:
            raise RuntimeError("; ".join(failures))

    @classmethod
    def list_splits(cls) -> list[str]:
        return ["train"]

    @classmethod
    def list_tasks(cls, split: str) -> list[dict]:
        if split != "train":
            return []
        return [
            {"task_name": d.name}
            for d in sorted(TASKS_ROOT.iterdir())
            if d.is_dir() and (d / "task_config.json").exists()
        ]


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    Server([ToolathlonGym]).run(port=_get_server_port())
