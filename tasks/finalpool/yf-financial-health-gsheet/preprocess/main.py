"""Prepare task data for yf-financial-health-gsheet.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import psycopg2


DB_CONN = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": "toolathlon_gym",
    "user": "eigent",
    "password": "camel",
}


def setup_gsheet():
    """Clear all gsheet tables so the agent starts fresh."""
    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = True
    cur = conn.cursor()

    print("Clearing gsheet tables ...")
    cur.execute("DELETE FROM gsheet.cells;")
    cur.execute("DELETE FROM gsheet.permissions;")
    cur.execute("DELETE FROM gsheet.sheets;")
    cur.execute("DELETE FROM gsheet.spreadsheets;")
    cur.execute("DELETE FROM gsheet.folders;")
    print("  -> All gsheet tables cleared.")

    cur.close()
    conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    # 1. Clear Google Sheet data in database
    setup_gsheet()

    # 2. Set up mock HTTP server

    print("\nPreprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
