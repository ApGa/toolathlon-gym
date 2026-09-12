"""Prepare task data for terminal-pw-howtocook-excel-word-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import os
import argparse, json, os, sys, shutil, tarfile, subprocess, time, uuid

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
    # Inject gcal noise
    cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
        (str(uuid.uuid4()), "Team Standup", "Regular standup meeting", "2026-03-05 09:00:00", "2026-03-05 09:30:00"))
    cur.execute("INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
        (str(uuid.uuid4()), "Lunch Break Yoga", "Wellness activity", "2026-03-06 12:00:00", "2026-03-06 12:45:00"))
    conn.commit()
    print("[preprocess] Injected noise data.")
    cur.close()
    conn.close()


def inject_data(launch_time):
    """No writable data injection needed for this task."""
    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False, default="2026-03-07 10:00:00")
    args = parser.parse_args()

    clear_writable_schemas()
    inject_data(args.launch_time)


if __name__ == "__main__":
    main()
