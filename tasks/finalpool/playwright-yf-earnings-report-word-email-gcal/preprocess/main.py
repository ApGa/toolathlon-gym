"""Prepare task data for playwright-yf-earnings-report-word-email-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import glob as globmod
import os
import uuid
from datetime import datetime, timedelta

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_gcal(cur):
    print("[preprocess] Clearing GCal events...")
    cur.execute("DELETE FROM gcal.events")
    print("[preprocess] GCal events cleared.")


def clear_emails(cur):
    print("[preprocess] Clearing email data...")
    cur.execute("DELETE FROM email.attachments")
    try:
        cur.execute("DELETE FROM email.sent_log")
    except Exception:
        pass
    cur.execute("DELETE FROM email.messages")
    try:
        cur.execute("DELETE FROM email.drafts")
    except Exception:
        pass
    print("[preprocess] Email data cleared.")


def inject_noise_emails(cur):
    """Insert noise emails."""
    print("[preprocess] Injecting noise email data...")
    cur.execute("SELECT id FROM email.folders WHERE name = 'INBOX' LIMIT 1")
    row = cur.fetchone()
    if not row:
        return
    folder_id = row[0]
    noise = [
        ("Monthly Portfolio Rebalancing", "ops@company.com", '["trading@company.com"]',
         "Rebalancing complete. No action needed."),
        ("Compliance Alert: Insider Trading Window", "compliance@company.com", '["all-staff@company.com"]',
         "Trading blackout period begins April 10, 2026."),
    ]
    for subj, from_addr, to_addr, body in noise:
        cur.execute("""
            INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, body_text)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (folder_id, f"<noise-{uuid.uuid4()}@company.com>", subj, from_addr, to_addr, body))
    print("[preprocess] Noise emails injected.")


def inject_noise_gcal(cur, launch_dt):
    """Inject noise calendar events."""
    print("[preprocess] Injecting noise GCal events...")
    dt = (launch_dt + timedelta(days=45)).strftime('%Y-%m-%d')
    cur.execute(f"""
        INSERT INTO gcal.events (summary, description, start_datetime, end_datetime)
        VALUES ('Team Standup', 'Daily standup', '{dt} 09:00:00+00', '{dt} 09:15:00+00')
    """)
    print("[preprocess] Noise GCal events injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    launch_dt = datetime.strptime(args.launch_time, "%Y-%m-%d %H:%M:%S") if args.launch_time else datetime(2026, 3, 7)

    try:
        clear_gcal(cur)
        clear_emails(cur)
        inject_noise_emails(cur)
        inject_noise_gcal(cur, launch_dt)
        conn.commit()
        print("[preprocess] Database operations committed.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


    if args.agent_workspace:
        for pattern in ["Earnings_Preview_Report.docx", "Earnings_Data_Appendix.xlsx"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
