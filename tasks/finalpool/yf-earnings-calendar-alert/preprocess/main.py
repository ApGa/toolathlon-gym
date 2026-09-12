"""Prepare task data for yf-earnings-calendar-alert.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import psycopg2

DB = dict(host=os.environ.get("PGHOST", "localhost"), port=5432, dbname="toolathlon_gym", user="eigent", password="camel")


def clear_email():
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM email.attachments")
    cur.execute("DELETE FROM email.sent_log")
    cur.execute("DELETE FROM email.messages")
    # Ensure sent folder exists
    cur.execute("SELECT id FROM email.folders WHERE LOWER(name) LIKE '%sent%' LIMIT 1")
    if not cur.fetchone():
        cur.execute("INSERT INTO email.folders (name) VALUES ('Sent')")
    cur.close()
    conn.close()
    print("[preprocess] Cleared email tables.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    clear_email()
    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
