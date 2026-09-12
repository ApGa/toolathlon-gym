"""Prepare task data for terminal-fetch-sf-hr-gcal-excel-email.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import json
import os
import glob as globmod
import uuid
import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"), "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent", "password": "camel",
}


def clear_db():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM gcal.events")
        cur.execute("DELETE FROM email.attachments")
        cur.execute("DELETE FROM email.sent_log")
        cur.execute("DELETE FROM email.messages")
        try:
            cur.execute("DELETE FROM email.drafts")
        except Exception:
            pass
        conn.commit()
        print("[preprocess] Cleared gcal and email data.")

        # Inject email noise
        cur.execute("SELECT id FROM email.folders WHERE name='INBOX' LIMIT 1")
        row = cur.fetchone()
        if row:
            inbox_id = row[0]
        else:
            cur.execute("INSERT INTO email.folders (name) VALUES ('INBOX') RETURNING id")
            inbox_id = cur.fetchone()[0]
        noise_emails = [
            ("Weekly Staff Meeting", "admin@company.com", json.dumps(["all@company.com"]), "Meeting tomorrow at 10am."),
            ("Parking Update", "facilities@company.com", json.dumps(["all@company.com"]), "New regulations next month."),
        ]
        for subj, from_addr, to_addr, body in noise_emails:
            cur.execute("INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, body_text, is_read, date) VALUES (%s, %s, %s, %s, %s, %s, false, now())",
                (inbox_id, f"noise-{uuid.uuid4()}@company.com", subj, from_addr, to_addr, body))

        # Inject gcal noise
        cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
            (str(uuid.uuid4()), "Team Standup", "Regular standup meeting", "2026-03-05 09:00:00", "2026-03-05 09:30:00"))
        cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
            (str(uuid.uuid4()), "Lunch Break Yoga", "Wellness activity", "2026-03-06 12:00:00", "2026-03-06 12:45:00"))
        conn.commit()
        print("[preprocess] Injected noise data.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


def ensure_email_folder():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT id FROM email.folders WHERE name = 'INBOX' LIMIT 1")
    if not cur.fetchone():
        cur.execute("INSERT INTO email.folders (name) VALUES ('INBOX')")
        conn.commit()
    cur.close()
    conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    clear_db()
    ensure_email_folder()

    if args.agent_workspace:
        for pattern in ["Compensation_Benchmark_Report.xlsx", "compensation_analysis.py"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
