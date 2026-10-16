"""Prepare task data for yf-peer-benchmark-analysis.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import psycopg2

DB = dict(host=os.environ.get("PGHOST", "localhost"), port=5432, dbname="toolathlon_gym", user="eigent", password="camel")


def clear_gsheet():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM gsheet.cells")
    cur.execute("DELETE FROM gsheet.permissions")
    cur.execute("DELETE FROM gsheet.sheets")
    cur.execute("DELETE FROM gsheet.spreadsheets")
    cur.execute("DELETE FROM gsheet.folders")
    cur.close()
    conn.close()
    print("[preprocess] Cleared gsheet tables.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    clear_gsheet()
    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
