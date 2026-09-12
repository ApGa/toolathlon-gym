# Toolathlon Gym

[![OpenReward Environment](https://img.shields.io/badge/%E2%AD%90%20OpenReward-Environment-f7e6cc)](https://www.openreward.ai/Eigent/ToolathlonGym)

## Description

Toolathlon Gym is an ORS environment for evaluating multi-tool coordination capabilities, developed by [Eigent AI](https://github.com/eigent-ai/toolathlon_gym). It contains 503 tasks requiring agents to coordinate 4-8 MCP servers (out of 25) backed by PostgreSQL. Tasks span productivity workflows (spreadsheets, documents, email), data analysis (SQL, finance, web scraping), content management (YouTube, Google Forms, Notion), and system administration (terminal, filesystem).

## Capabilities

- Multi-tool coordination across 25 MCP servers
- Database querying via Snowflake/PostgreSQL
- Document creation (Excel, Word, PowerPoint, PDF)
- Web scraping and browser automation via Playwright
- Email, calendar, and forms management
- File system operations and Python code execution

## Compute Requirements

Each task runs inside a Docker container with PostgreSQL, 25 MCP server implementations, Node.js 22, and Python 3.12. The ORS server runs alongside all services in a single container.

## Self-Hosting

Run the environment server on the default port:

```bash
docker run --rm -p 8080:8080 ghcr.io/apga/openreward-toolathlon-gym:latest
```

To use a different in-container port, set `OPENREWARD_PORT` and publish the same container port:

```bash
docker run --rm \
  -e OPENREWARD_PORT=18080 \
  -p 18080:18080 \
  ghcr.io/apga/openreward-toolathlon-gym:latest
```

`OPENREWARD_PORT` is preferred over the generic `PORT` variable. If `OPENREWARD_PORT` is unset, the server falls back to `PORT`, then `8080`.

For higher rollout concurrency, set the number of in-container ORS workers:

```bash
docker run --rm \
  -e OPENREWARD_WORKERS=64 \
  -e OPENREWARD_PG_MAX_CONNECTIONS=2000 \
  -p 8080:8080 \
  ghcr.io/apga/openreward-toolathlon-gym:latest
```

If `OPENREWARD_WORKERS` is unset, the container defaults to the host CPU count with a minimum of 4 workers. There is no fixed upper cap; make sure `OPENREWARD_PG_MAX_CONNECTIONS`, CPU, and memory are sized for the number of concurrent sessions.

### Concurrent task setup and HTTP fixtures

Each episode copies its task into a private, writable temporary directory. The
127 tasks with local HTTP data declare an `http_fixture` in `task_config.json`:

```json
"http_fixture": {
  "port": 30180,
  "source": "files/mock_pages.tar.gz",
  "root": "mock_pages"
}
```

`port` identifies the original URLs in the task assets. The environment serves
each episode's private fixture on an OS-assigned loopback port and updates its
instructions, scripts, text inputs, PDFs, Office documents, and fixture links to
use that endpoint. Legacy URLs in tool arguments and results are also translated.
The endpoint stays alive until the episode is torn down. Preprocessors only
prepare task data; they must not start servers or kill processes on fixed ports.

This works with read-only task assets and an unprivileged container user,
including Apptainer and Enroot deployments. Concurrent episodes can share the
host network without overwriting or terminating each other's fixtures. The
server, MCP tools, and Python execution must share a network namespace and have
access to writable temporary and workspace directories.

Setup checks that the fixture serves its actual data. A failed or timed-out
preprocessor rejects the episode setup and cleans up its resources. The server
logs the task, session, and the last 8 KiB of subprocess output. The preprocessing
timeout is 30 seconds; this is separate from the agent's rollout time limit.

Run the regression suite with Python 3.12 and the server dependencies installed:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## License

[Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0).

## Tasks

There is one split in this environment:

- **test**: 503 tasks

Tasks require coordinating 4-8 MCP servers per task across domains including YouTube analysis, spreadsheet automation, database querying, web scraping, document generation, email management, and calendar scheduling.

## Reward Structure

This is a multi-turn environment with script-based validation. The agent uses MCP tools and built-in tools to complete tasks, then calls `claim_done` to run the evaluation script. By default, the reward is binary: 1.0 if the grader passes, 0.0 otherwise.

To return fractional rewards from grader check counts where available, set:

```bash
docker run --rm \
  -e OPENREWARD_REWARD_MODE=partial \
  -p 8080:8080 \
  ghcr.io/apga/openreward-toolathlon-gym:latest
```

Partial mode reads the grader result log or stdout summaries such as `total_passed/total_checks`, `passed/failed`, or `Overall: N/M checks passed`. If a grader only reports pass/fail, the server falls back to the binary reward for that task.

## Data

Data consists of 503 task directories sourced from [GitHub eigent-ai/toolathlon_gym](https://github.com/eigent-ai/toolathlon_gym). Each task includes `task_config.json`, `docs/task.md`, `preprocess/main.py`, `evaluation/main.py`, and initial workspace files. PostgreSQL is seeded with data across 6 schemas (Canvas LMS, HR/Sales, e-commerce, finance, YouTube, rail).

## Tools

Shared tools (always available):

| Tool | Description |
|------|-------------|
| `claim_done` | Run evaluation and get reward. Ends the episode. |
| `python_execute` | Execute Python code in the workspace. |

Task-specific tools (per-task, from MCP servers):

Each task exposes tools from its required MCP servers. Examples include `read_file`, `list_spreadsheets`, `search_videos`, `run_command`, `list_databases`, `browser_navigate`, and ~300 others across 25 servers.

## Time Horizon

Multi-turn. Agents read task instructions, use MCP tools to gather data, create files, query databases, and perform analysis, then call `claim_done` for evaluation.

## Environment Difficulty

Tasks require coordinating multiple tools across different domains. Most tasks involve 4-8 MCP servers and require multi-step reasoning, data transformation, and file generation.

## Other Environment Requirements

None.

## Safety

Agents operate within Docker containers with isolated PostgreSQL databases and per-session workspaces. MCP servers are scoped to the agent's workspace directory.

## Citation

```bibtex
@misc{toolathlon,
  author    = {Eigent AI},
  title     = {{Toolathlon Gym: A Multi-Tool Benchmark for LLM Agents}},
  year      = {2025},
  url       = {https://github.com/eigent-ai/toolathlon_gym}
}
```
