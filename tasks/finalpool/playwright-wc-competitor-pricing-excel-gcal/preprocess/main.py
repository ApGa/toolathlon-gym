"""Prepare task data for playwright-wc-competitor-pricing-excel-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
import glob as globmod
import os

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


def clear_gcal(cur):
    print("[preprocess] Clearing Google Calendar events...")
    cur.execute("DELETE FROM gcal.events")
    print("[preprocess] Google Calendar events cleared.")


def inject_noise_data(cur):
    """Insert noise data so the agent must filter correctly."""
    print("[preprocess] Injecting noise data...")
    # Noise emails
    noise_emails = [
        ("Weekly Team Standup Notes", "team@ecommerce.com",
         '["all@ecommerce.com"]', "Here are this week's standup notes."),
        ("Server Maintenance Window", "ops@ecommerce.com",
         '["devs@ecommerce.com"]', "Maintenance scheduled for this weekend."),
    ]
    for subj, from_addr, to_addr, body in noise_emails:
        cur.execute("""
            INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, body_text)
            VALUES ((SELECT id FROM email.folders WHERE name = 'INBOX' LIMIT 1),
                    %s, %s, %s, %s, %s)
        """, (f"<noise-{subj.replace(' ','-').lower()}@ecommerce.com>", subj, from_addr, to_addr, body))

    # Noise calendar events
    noise_events = [
        ("Team Standup", "Daily standup meeting", "2026-03-10 09:00:00+00", "2026-03-10 09:30:00+00"),
        ("Sprint Planning", "Q2 sprint planning", "2026-03-13 14:00:00+00", "2026-03-13 16:00:00+00"),
    ]
    for i, (summary, desc, start, end) in enumerate(noise_events):
        cur.execute("""
            INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime)
            VALUES (%s, %s, %s, %s, %s)
        """, (f"noise-event-{i}", summary, desc, start, end))
    print("[preprocess] Noise data injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_emails(cur)
        clear_gcal(cur)
        inject_noise_data(cur)
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
        for pattern in ["Competitive_Pricing_Analysis.xlsx"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
