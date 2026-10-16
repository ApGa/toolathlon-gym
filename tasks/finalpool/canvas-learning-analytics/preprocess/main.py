"""Prepare task data for canvas-learning-analytics.

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
        cur.execute("DELETE FROM notion.comments")
        cur.execute("DELETE FROM notion.blocks")
        cur.execute("DELETE FROM notion.pages")
        cur.execute("DELETE FROM notion.databases")
        cur.execute("DELETE FROM notion.users")
        print("[preprocess] Cleared Notion data.")

        # Insert a parent page for the dashboard
        cur.execute("""
            INSERT INTO notion.pages (id, object, properties, archived, in_trash)
            VALUES ('parent-analytics-page', 'page',
                    '{"title": {"title": [{"text": {"content": "Academic Reports"}}]}}'::jsonb,
                    false, false)
        """)
        print("[preprocess] Inserted parent page for Notion dashboard.")

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
