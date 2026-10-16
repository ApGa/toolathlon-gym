"""Prepare task data for howtocook-wellness-tracker.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import json
import os
import uuid

import psycopg2

DB_CONN = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_and_setup_notion(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM notion.comments")
        cur.execute("DELETE FROM notion.blocks")
        cur.execute("DELETE FROM notion.pages")
        cur.execute("DELETE FROM notion.databases")
        cur.execute("DELETE FROM notion.users")
        # Insert parent page
        page_id = f"wellness-parent-{uuid.uuid4().hex[:8]}"
        props = json.dumps({"title": [{"text": {"content": "Health Coach Knowledge Base"}}]})
        cur.execute(
            "INSERT INTO notion.pages (id, object, properties, created_time, last_edited_time) "
            "VALUES (%s, %s, %s::jsonb, NOW(), NOW())", (page_id, "page", props))
    conn.commit()
    print("[preprocess] Cleared Notion and inserted parent page")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONN)
    try:
        clear_and_setup_notion(conn)
    finally:
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
