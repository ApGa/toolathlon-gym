"""Run task preprocessing with bounded lifetime and useful failure evidence."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys


def _log_tail(path: Path, limit: int = 8192) -> str:
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


async def run_preprocess(
    script: Path, workspace: Path, log_path: Path, *,
    task: str, session: str, launch_time: str, env: dict[str, str],
    timeout: float = 30,
) -> None:
    if not script.exists():
        return
    failure = None
    with log_path.open("wb") as log:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-u", str(script),
            "--agent_workspace", str(workspace), "--launch_time", launch_time,
            cwd=str(script.parent), env=env,
            stdout=log, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            if proc.returncode != 0:
                failure = f"exit status {proc.returncode}"
        except asyncio.TimeoutError:
            failure = f"timeout after {timeout:g}s"
        finally:
            # No persistent service belongs to preprocessing anymore. Own the
            # process group, including children that outlive their parent; never
            # identify or kill processes by their listening port.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
    if failure is not None:
        tail = _log_tail(log_path)
        print(json.dumps({
            "event": "preprocess_failed", "task": task, "session": session,
            "reason": failure, "output_tail": tail,
        }), flush=True)
        raise RuntimeError(f"Preprocessing failed for task={task} session={session}: {failure}\n{tail}")
