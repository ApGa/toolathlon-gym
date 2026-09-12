"""Prepare task data for fetch-sf-support-survey-gform-gcal-excel.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
import glob as globmod
import os

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent",
    "password": "camel",
}


def clear_gcal(cur):
    print("[preprocess] Clearing Google Calendar events...")
    cur.execute("DELETE FROM gcal.events")
    print("[preprocess] Google Calendar events cleared.")


def clear_gform(cur):
    print("[preprocess] Clearing Google Forms data...")
    cur.execute("DELETE FROM gform.responses")
    cur.execute("DELETE FROM gform.questions")
    cur.execute("DELETE FROM gform.forms")
    print("[preprocess] Google Forms data cleared.")


def inject_noise_data(cur):
    """Insert noise data so the agent must filter correctly."""
    print("[preprocess] Injecting noise data...")
    # Noise calendar events
    noise_events = [
        ("All-Hands Meeting", "Company all-hands", "2026-03-20 14:00:00+00", "2026-03-20 15:00:00+00"),
        ("Product Launch Sync", "Sync on upcoming launch", "2026-03-22 10:00:00+00", "2026-03-22 11:00:00+00"),
    ]
    for i, (summary, desc, start, end) in enumerate(noise_events):
        cur.execute("""
            INSERT INTO gcal.events (id, summary, description, start_datetime, end_datetime)
            VALUES (%s, %s, %s, %s, %s)
        """, (f"noise-support-event-{i}", summary, desc, start, end))

    # Noise Google Form
    cur.execute("""
        INSERT INTO gform.forms (id, title, document_title, description)
        VALUES (%s, %s, %s, %s)
    """, ("noise-form-001", "Employee Satisfaction Survey", "Employee Satisfaction Survey",
          "Internal employee feedback form"))
    print("[preprocess] Noise data injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_gcal(cur)
        clear_gform(cur)
        inject_noise_data(cur)
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
        for pattern in ["Support_Satisfaction_Analysis.xlsx"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
