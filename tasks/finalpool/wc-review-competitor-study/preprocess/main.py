"""Prepare task data for wc-review-competitor-study.

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


def clear_notion(cur):
    """Clear Notion data and inject parent page."""
    print("[preprocess] Clearing Notion data...")
    cur.execute("DELETE FROM notion.comments")
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")

    # Inject noise data - a few unrelated pages
    cur.execute("""INSERT INTO notion.pages (id, object, properties, url, archived, in_trash)
        VALUES ('page-kb-root', 'page',
        '{"title": {"title": [{"text": {"content": "Knowledge Base"}}]}}',
        'https://notion.so/kb-root', false, false)""")
    cur.execute("""INSERT INTO notion.pages (id, object, properties, url, archived, in_trash,
        parent)
        VALUES ('page-old-analysis', 'page',
        '{"title": {"title": [{"text": {"content": "Old Q4 2025 Analysis"}}]}}',
        'https://notion.so/old-analysis', false, false,
        '{"type": "page_id", "page_id": "page-kb-root"}')""")
    print("[preprocess] Notion data cleared and parent pages injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        clear_notion(cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
