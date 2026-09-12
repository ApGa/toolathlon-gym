"""Prepare task data for wc-product-launch-dashboard.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
import os

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


async def main():
    # No writable schemas to DELETE - read-only data sources
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()


    if args.agent_workspace:
        for fname in ["Product_Review.xlsx", "Q1_Product_Review.pptx"]:
            fpath = os.path.join(args.agent_workspace, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
                print(f"[preprocess] Removed {fpath}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
