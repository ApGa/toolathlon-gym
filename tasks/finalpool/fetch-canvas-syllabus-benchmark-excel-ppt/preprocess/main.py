"""Prepare task data for fetch-canvas-syllabus-benchmark-excel-ppt.

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


def clear_emails(cur):
    print("[preprocess] Clearing email data...")
    cur.execute("DELETE FROM email.attachments")
    cur.execute("DELETE FROM email.sent_log")
    cur.execute("DELETE FROM email.messages")
    try:
        cur.execute("DELETE FROM email.drafts")
    except Exception:
        pass
    print("[preprocess] Email data cleared.")


def inject_noise_emails(cur):
    print("[preprocess] Injecting noise email data...")
    noise = [
        ("Faculty Meeting Reminder", "admin@university.edu",
         '["faculty@university.edu"]', "Reminder: Faculty meeting Friday at 3 PM."),
        ("Campus Parking Update", "facilities@university.edu",
         '["all@university.edu"]', "New parking regulations effective next month."),
        ("Library Extended Hours", "library@university.edu",
         '["students@university.edu"]', "The library will have extended hours during finals."),
    ]
    for subj, from_addr, to_addr, body in noise:
        cur.execute("""
            INSERT INTO email.messages (folder_id, message_id, subject, from_addr, to_addr, body_text)
            VALUES ((SELECT id FROM email.folders WHERE name = 'INBOX' LIMIT 1),
                    %s, %s, %s, %s, %s)
        """, (f"<noise-{subj.replace(' ','-').lower()}@university.edu>", subj, from_addr, to_addr, body))
    print("[preprocess] Noise emails injected.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_emails(cur)
        inject_noise_emails(cur)
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
        for pattern in ["Course_Benchmark_Analysis.xlsx", "Academic_Benchmark_Presentation.pptx"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
