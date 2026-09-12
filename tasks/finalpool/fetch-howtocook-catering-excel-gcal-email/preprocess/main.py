"""Prepare task data for fetch-howtocook-catering-excel-gcal-email.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import os
import argparse, json, os, sys, shutil, tarfile, subprocess, time  # noqa: E401
from datetime import datetime, timedelta

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"), "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent", "password": "camel"
}


def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def clear_writable_schemas():
    conn = get_conn()
    cur = conn.cursor()
    # Clear gcal events
    cur.execute("DELETE FROM gcal.events")
    # Clear email dependent tables first
    cur.execute("DELETE FROM email.sent_log")
    cur.execute("DELETE FROM email.attachments")
    cur.execute("DELETE FROM email.drafts")
    # Clear email messages
    cur.execute("DELETE FROM email.messages")
    conn.commit()
    cur.close()
    conn.close()


def inject_data(launch_time):
    conn = get_conn()
    cur = conn.cursor()
    launch_dt = datetime.strptime(launch_time, "%Y-%m-%d %H:%M:%S")

    # Inject 2 noise calendar events
    cur.execute("""
        INSERT INTO gcal.events (summary, start_datetime, end_datetime, description, location)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        "Team Standup",
        (launch_dt + timedelta(days=1)).strftime("%Y-%m-%d 09:00:00"),
        (launch_dt + timedelta(days=1)).strftime("%Y-%m-%d 09:30:00"),
        "Daily team standup meeting",
        "Conference Room A"
    ))
    cur.execute("""
        INSERT INTO gcal.events (summary, start_datetime, end_datetime, description, location)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        "Project Review",
        (launch_dt + timedelta(days=3)).strftime("%Y-%m-%d 14:00:00"),
        (launch_dt + timedelta(days=3)).strftime("%Y-%m-%d 15:00:00"),
        "Quarterly project review",
        "Conference Room B"
    ))

    # Get inbox folder id
    cur.execute("SELECT id FROM email.folders WHERE name = 'Inbox' OR name = 'INBOX' LIMIT 1")
    folder_row = cur.fetchone()
    folder_id = folder_row[0] if folder_row else 3075

    # Inject 1 noise email
    cur.execute("""
        INSERT INTO email.messages (folder_id, from_addr, to_addr, subject, body_text, date)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        folder_id,
        "hr@company.com",
        json.dumps(["all_staff@company.com"]),
        "Office Holiday Schedule",
        "Please note the updated holiday schedule for Q2 2026.",
        (launch_dt - timedelta(days=2)).strftime("%Y-%m-%d 10:00:00")
    ))

    conn.commit()
    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False, default="2026-03-07 10:00:00")
    args = parser.parse_args()

    clear_writable_schemas()
    inject_data(args.launch_time)


if __name__ == "__main__":
    main()
