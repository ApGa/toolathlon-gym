"""Prepare task data for terminal-sf-fetch-excel-notion-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import glob as globmod
import os
import uuid

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"), "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent", "password": "camel",
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    # 2. Clear notion and gcal, inject noise
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM notion.blocks")
        cur.execute("DELETE FROM notion.comments")
        cur.execute("DELETE FROM notion.pages")
        cur.execute("DELETE FROM notion.databases")
        cur.execute("DELETE FROM gcal.events")
        conn.commit()
        print("[preprocess] Cleared notion and gcal data.")

        # Inject noise gcal events
        noise_events = [
            ("Sprint Planning", "2026-04-06 09:00:00", "2026-04-06 10:00:00", "Bi-weekly sprint planning"),
            ("1:1 with Manager", "2026-04-07 11:00:00", "2026-04-07 11:30:00", "Regular check-in"),
        ]
        for summary, start, end, desc in noise_events:
            cur.execute("""
                INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status)
                VALUES (%s, %s, %s, %s, %s, 'confirmed')
            """, (str(uuid.uuid4()), summary, desc, start, end))

        # Inject noise notion database
        noise_db_id = str(uuid.uuid4())
        import json
        cur.execute("""
            INSERT INTO notion.databases (id, title, description, properties, parent, created_time, last_edited_time)
            VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, NOW(), NOW())
        """, (noise_db_id,
              json.dumps([{"type": "text", "text": {"content": "Project Tracker"}}]),
              json.dumps([{"type": "text", "text": {"content": "Track project milestones"}}]),
              json.dumps({"Name": {"type": "title", "title": {}}}),
              json.dumps({"type": "workspace", "workspace": True})))

        conn.commit()
        print("[preprocess] Injected noise data.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    # Clean up agent workspace
    if args.agent_workspace:
        for pattern in ["SLA_Compliance_Report.xlsx", "sla_analyzer.py"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
