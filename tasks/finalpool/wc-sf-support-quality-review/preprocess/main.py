"""Prepare task data for wc-sf-support-quality-review.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import shutil


async def main():
    # No writable schemas to DELETE - read-only data sources
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()


    if args.agent_workspace:
        initial_ws = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "initial_workspace"
        )
        for f in os.listdir(initial_ws):
            src = os.path.join(initial_ws, f)
            if os.path.isfile(src) and not f.startswith("."):
                shutil.copy2(src, os.path.join(args.agent_workspace, f))
        print(f"[preprocess] Copied initial_workspace files to {args.agent_workspace}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
