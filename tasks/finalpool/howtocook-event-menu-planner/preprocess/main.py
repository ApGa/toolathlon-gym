"""Prepare task data for howtocook-event-menu-planner.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os

import psycopg2

DB_CONN = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_email(conn):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM email.sent_log")
        cur.execute("DELETE FROM email.attachments")
        cur.execute("DELETE FROM email.messages")
        cur.execute("DELETE FROM email.drafts")
        cur.execute("DELETE FROM email.folders")
        cur.execute("DELETE FROM email.account_config")
        # Set up email account
        cur.execute("INSERT INTO email.account_config (email, name) VALUES (%s, %s)",
                    ("events@company.com", "Event Planning Team"))
        cur.execute("INSERT INTO email.folders (name, delimiter, flags) VALUES (%s, %s, %s)",
                    ("INBOX", "/", '["\\\\Inbox"]'))
        cur.execute("INSERT INTO email.folders (name, delimiter, flags) VALUES (%s, %s, %s)",
                    ("Sent", "/", '["\\\\Sent"]'))
    conn.commit()
    print("[preprocess] Cleared and configured email")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONN)
    try:
        clear_email(conn)
    finally:
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
