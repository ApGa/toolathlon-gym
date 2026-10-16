"""Prepare task data for terminal-sf-fetch-support-ppt-email.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import json
import os
import uuid

import psycopg2

DB = dict(host=os.environ.get("PGHOST", "localhost"), port=5432,
          dbname=os.environ.get("PGDATABASE", "toolathlon_gym"),
          user="eigent", password="camel")


def clear_and_inject_email():
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM email.attachments")
        cur.execute("DELETE FROM email.sent_log")
        cur.execute("DELETE FROM email.messages")
        cur.execute("DELETE FROM email.drafts")
        conn.commit()
        print("[preprocess] Cleared email schema.")

        cur.execute("SELECT id FROM email.folders WHERE name='INBOX' LIMIT 1")
        inbox_id = cur.fetchone()[0]

        noise = [
            ("Q1 Support Budget Approved", "finance@company.example.com",
             json.dumps(["support@company.example.com"]),
             "The Q1 support budget has been approved. Please review the allocation."),
            ("New Knowledge Base Articles Published", "kb@company.example.com",
             json.dumps(["agents@company.example.com"]),
             "Five new knowledge base articles have been published this week."),
            ("Office Closure Next Friday", "hr@company.example.com",
             json.dumps(["all@company.example.com"]),
             "Reminder that the office will be closed next Friday for the holiday."),
        ]
        for subj, from_addr, to_addr, body in noise:
            cur.execute(
                "INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, "
                "body_text, is_read, date) VALUES (%s, %s, %s, %s, %s, %s, false, now())",
                (inbox_id, f"noise-{uuid.uuid4()}@company.example.com", subj, from_addr, to_addr, body)
            )
        conn.commit()
        print("[preprocess] Injected 3 noise emails.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    clear_and_inject_email()
    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
