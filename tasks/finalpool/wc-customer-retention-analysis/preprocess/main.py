"""Prepare task data for wc-customer-retention-analysis.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
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
    cur.execute("DELETE FROM email.folders")
    print("[preprocess] Email data cleared.")


def clear_gsheet(cur):
    print("[preprocess] Clearing Google Sheet data...")
    for table in ["cells", "sheets", "spreadsheets", "permissions", "folders"]:
        cur.execute(f'DELETE FROM gsheet."{table}"')
    print("[preprocess] Google Sheet data cleared.")


def inject_email_folders(cur):
    print("[preprocess] Injecting email folders...")
    folders = [
        ("INBOX", "/"),
        ("Sent", "/"),
        ("Drafts", "/"),
    ]
    for name, delim in folders:
        cur.execute(
            "INSERT INTO email.folders (name, delimiter) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (name, delim))
    print("[preprocess] Email folders injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_emails(cur)
        clear_gsheet(cur)
        inject_email_folders(cur)
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
        for fname in ["Customer_Retention.xlsx"]:
            fpath = os.path.join(args.agent_workspace, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
                print(f"[preprocess] Removed {fpath}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
