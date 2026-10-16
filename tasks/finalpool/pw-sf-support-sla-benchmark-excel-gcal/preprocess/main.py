"""Prepare task data for pw-sf-support-sla-benchmark-excel-gcal.

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
    
    cur.execute("DELETE FROM gcal.events")
    conn.commit()
    cur.close()
    conn.close()

def inject_data(launch_time):
    conn = get_conn()
    cur = conn.cursor()
    launch_dt = datetime.strptime(launch_time, "%Y-%m-%d %H:%M:%S")
    
    # Inject noise calendar events
    cur.execute("""INSERT INTO gcal.events (summary, start_datetime, end_datetime, description, status)
        VALUES ('Team Standup', %s, %s, 'Daily standup meeting', 'confirmed')""",
        (launch_dt.replace(hour=9, minute=0), launch_dt.replace(hour=9, minute=15)))
    cur.execute("""INSERT INTO gcal.events (summary, start_datetime, end_datetime, description, status)
        VALUES ('Lunch Break', %s, %s, 'Lunch', 'confirmed')""",
        (launch_dt.replace(hour=12, minute=0), launch_dt.replace(hour=13, minute=0)))
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
