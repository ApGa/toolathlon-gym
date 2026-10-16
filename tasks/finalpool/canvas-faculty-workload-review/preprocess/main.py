"""Prepare task data for canvas-faculty-workload-review.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        cur.execute("DELETE FROM gsheet.cells")
        cur.execute("DELETE FROM gsheet.permissions")
        cur.execute("DELETE FROM gsheet.sheets")
        cur.execute("DELETE FROM gsheet.spreadsheets")
        cur.execute("DELETE FROM gsheet.folders")
        print("[preprocess] Cleared Google Sheet data.")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Database error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
