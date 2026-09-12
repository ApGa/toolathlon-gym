"""Prepare task data for sf-customer-health-dashboard.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import glob as globmod
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


def clear_schemas(cur):
    print("[preprocess] Clearing Notion data...")
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.comments")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")

    print("[preprocess] Clearing GCal events...")
    cur.execute("DELETE FROM gcal.events")

    print("[preprocess] Schemas cleared.")


def inject_notion_parent(cur):
    """Inject a parent page so the agent can create child pages."""
    print("[preprocess] Injecting Notion parent page...")
    page_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO notion.pages (id, object, parent, properties, archived, in_trash)
        VALUES (%s, 'page', %s, %s, false, false)
    """, (
        page_id,
        json.dumps({"type": "workspace", "workspace": True}),
        json.dumps({"title": {"title": [{"text": {"content": "Customer Success"}}]}}),
    ))
    print(f"[preprocess] Injected parent page: {page_id}")
    return page_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        clear_schemas(cur)
        inject_notion_parent(cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


    if args.agent_workspace:
        for pattern in ["Customer_Health.xlsx"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    main()
