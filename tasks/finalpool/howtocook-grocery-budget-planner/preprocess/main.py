"""Prepare task data for howtocook-grocery-budget-planner.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os

import psycopg2

DB_CONN = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_schemas(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gsheet.cells")
        cur.execute("DELETE FROM gsheet.sheets")
        cur.execute("DELETE FROM gsheet.spreadsheets")
        cur.execute("DELETE FROM gcal.events")
    conn.commit()
    print("[preprocess] Cleared gsheet, gcal")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONN)
    try:
        clear_schemas(conn)
    finally:
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
