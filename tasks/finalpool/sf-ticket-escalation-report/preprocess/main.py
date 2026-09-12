"""Prepare task data for sf-ticket-escalation-report.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import json
import os
import uuid

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_emails(cur):
    print("[preprocess] Clearing email data...")
    cur.execute("DELETE FROM email.attachments")
    cur.execute("DELETE FROM email.sent_log")
    cur.execute("DELETE FROM email.messages")
    try:
        cur.execute("DELETE FROM email.drafts")
    except Exception:
        pass
    print("[preprocess] Email data cleared.")


def clear_notion(cur):
    print("[preprocess] Clearing Notion data...")
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.comments")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")
    print("[preprocess] Notion data cleared.")


def inject_notion_parent(cur):
    """Inject a parent page for the agent to create the escalation page under."""
    print("[preprocess] Injecting Notion parent page...")
    page_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO notion.pages (id, object, parent, properties, archived, in_trash)
        VALUES (%s, 'page', %s, %s, false, false)
    """, (
        page_id,
        json.dumps({"type": "workspace", "workspace": True}),
        json.dumps({"title": {"title": [{"text": {"content": "Support Team"}}]}}),
    ))
    print(f"[preprocess] Notion parent page created: {page_id}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_emails(cur)
        clear_notion(cur)
        inject_notion_parent(cur)
        conn.commit()
        print("[preprocess] DB operations committed.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
