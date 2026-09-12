"""Prepare task data for canvas-assessment-quality-audit.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio


async def main():
    # No writable schemas to DELETE - read-only data sources
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
