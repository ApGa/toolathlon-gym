"""Prepare task data for terminal-wc-pw-pricing-excel-word-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import glob as globmod
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


def clear_gcal(cur):
    print("[preprocess] Clearing Google Calendar events...")
    cur.execute("DELETE FROM gcal.events")
    print("[preprocess] Google Calendar events cleared.")
    # Inject gcal noise
    cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
        (str(uuid.uuid4()), "Team Standup", "Regular standup meeting", "2026-03-05 09:00:00", "2026-03-05 09:30:00"))
    cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
        (str(uuid.uuid4()), "Lunch Break Yoga", "Wellness activity", "2026-03-06 12:00:00", "2026-03-06 12:45:00"))
    print("[preprocess] Injected noise data.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_gcal(cur)
        conn.commit()
        print("[preprocess] DB operations committed.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


    if args.agent_workspace:
        for pattern in ["Competitive_Pricing_Report.xlsx", "Pricing_Strategy_Report.docx", "price_analysis_output.txt"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
