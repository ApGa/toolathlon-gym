"""Prepare task data for howtocook-nutrition-benchmark.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio


async def main():
    # No writable schemas to DELETE - read-only data sources
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
