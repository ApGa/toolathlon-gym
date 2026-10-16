"""Run a task tool with bounded output and clean up timed-out subprocesses."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import signal


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


def process_group_is_alive(pgid: int) -> bool:
    """Check for running members, excluding exited Linux zombie processes.

    killpg(pgid, 0) also succeeds for zombie-only groups. Sending SIGKILL to
    those groups cannot remove them; they must be reaped by their parent.
    This check is used by the PBS MCP cleanup layer after closing the leader.
    """
    if pgid <= 0:
        raise ValueError("Expected a positive, owned process-group ID")
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if os.getpgid(int(directory.name)) != pgid:
                continue
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pgid and fields[0] not in ("Z", "X", "x"):
            return True
    return False


async def _read_output(stream, limit: int) -> str:
    chunks = []
    retained = 0
    truncated = False
    while chunk := await stream.read(65536):
        remaining = max(0, limit - retained)
        if remaining:
            chunks.append(chunk[:remaining])
        retained += min(len(chunk), remaining)
        truncated |= len(chunk) > remaining
    text = b"".join(chunks).decode("utf-8", errors="replace")
    if truncated:
        text += "\n[Output truncated; write large results to a workspace file.]\n"
    return text


async def run_task_process(
    *args: str, cwd: str, env: dict[str, str], timeout: float,
    output_limit: int = 1024 * 1024,
) -> ProcessResult:
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env, start_new_session=True,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    tasks = [
        asyncio.create_task(_read_output(proc.stdout, output_limit)),
        asyncio.create_task(_read_output(proc.stderr, output_limit)),
        asyncio.create_task(proc.wait()),
    ]
    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=timeout,
        )
        return ProcessResult(returncode, stdout, stderr)
    except BaseException:
        # wait_for cancels the reader, not the operating-system process. Own
        # a separate group so descendants cannot keep computing after timeout
        # or cancellation, and other episodes are unaffected.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
