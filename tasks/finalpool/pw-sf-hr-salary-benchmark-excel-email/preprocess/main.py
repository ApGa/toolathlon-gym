"""Prepare task data for pw-sf-hr-salary-benchmark-excel-email.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import os
import argparse, json, os, sys, shutil, tarfile, subprocess, time
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
    
    cur.execute("DELETE FROM email.attachments")
    cur.execute("DELETE FROM email.sent_log")
    cur.execute("DELETE FROM email.messages WHERE folder_id != (SELECT id FROM email.folders WHERE name='INBOX' LIMIT 1) OR TRUE")
    cur.execute("DELETE FROM email.messages")
    conn.commit()
    cur.close()
    conn.close()

def inject_data(launch_time):
    conn = get_conn()
    cur = conn.cursor()
    launch_dt = datetime.strptime(launch_time, "%Y-%m-%d %H:%M:%S")
    
    # Inject noise emails
    inbox_id = 1
    cur.execute("""SELECT id FROM email.folders WHERE name = 'INBOX' LIMIT 1""")
    row = cur.fetchone()
    if row:
        inbox_id = row[0]
    cur.execute("""INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, date, body_text, is_read)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (inbox_id, '<noise-001@company.com>', 'Team Lunch Friday', 'admin@company.com',
         json.dumps(['all-staff@company.com']), launch_dt - timedelta(hours=5),
         'Reminder: Team lunch this Friday at noon in the cafeteria.', True))
    cur.execute("""INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, date, body_text, is_read)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (inbox_id, '<noise-002@company.com>', 'Parking Lot Maintenance', 'facilities@company.com',
         json.dumps(['all-staff@company.com']), launch_dt - timedelta(hours=8),
         'The parking lot will be resurfaced this weekend. Please use the north entrance.', True))
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
