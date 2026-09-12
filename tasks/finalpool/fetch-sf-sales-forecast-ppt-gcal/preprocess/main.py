"""Prepare task data for fetch-sf-sales-forecast-ppt-gcal.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
import os
import uuid

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_gcal(cur):
    """Clear all Google Calendar events."""
    print("[preprocess] Clearing Google Calendar events...")
    cur.execute("DELETE FROM gcal.events")
    print("[preprocess] Google Calendar events cleared.")


def inject_noise_gcal(cur):
    """Inject noise calendar event."""
    print("[preprocess] Injecting noise calendar event...")
    cur.execute(
        "INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime) "
        "VALUES (%s, %s, %s, %s, %s)",
        (
            f"noise-{uuid.uuid4()}",
            "Weekly Team Standup",
            "Regular weekly team standup meeting",
            "2026-03-10T09:00:00+00:00",
            "2026-03-10T09:30:00+00:00",
        ),
    )
    print("[preprocess] Noise calendar event injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_gcal(cur)
        inject_noise_gcal(cur)
        conn.commit()
        print("[preprocess] Database operations committed.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Database error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
