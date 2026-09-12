"""Prepare task data for yf-sector-outlook-report.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import json
import os
import psycopg2

DB = dict(host=os.environ.get("PGHOST", "localhost"), port=5432, dbname="toolathlon_gym", user="eigent", password="camel")


def clear_and_setup_notion():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.comments")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")
    # Insert a parent page for the research library
    cur.execute("""INSERT INTO notion.pages (properties, url) VALUES (%s::jsonb, %s)""",
                (json.dumps({"title": {"title": [{"type": "text", "text": {"content": "Research Library"}}]}}),
                 "https://notion.so/research-library"))
    cur.close()
    conn.close()
    print("[preprocess] Cleared notion tables and inserted parent page.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    clear_and_setup_notion()
    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
