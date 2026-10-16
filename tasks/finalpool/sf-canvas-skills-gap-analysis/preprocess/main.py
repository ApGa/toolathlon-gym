"""Prepare task data for sf-canvas-skills-gap-analysis.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import shutil

import psycopg2

DB = dict(
    host=os.environ.get("PGHOST", "localhost"),
    port=5432,
    dbname=os.environ.get("PGDATABASE", "toolathlon_gym"),
    user="postgres",
    password="postgres",
)


def clear_email(conn):
    cur = conn.cursor()
    cur.execute("DELETE FROM email.attachments")
    try:
        cur.execute("DELETE FROM email.sent_log")
    except Exception:
        conn.rollback()
    cur.execute("DELETE FROM email.messages")
    conn.commit()
    cur.close()
    print("[preprocess] Email data cleared.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB)
    clear_email(conn)
    conn.close()


    if args.agent_workspace:
        initial_ws = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "initial_workspace"
        )
        for f in os.listdir(initial_ws):
            src = os.path.join(initial_ws, f)
            if os.path.isfile(src) and not f.startswith("."):
                shutil.copy2(src, os.path.join(args.agent_workspace, f))
        print(f"[preprocess] Copied initial_workspace files to {args.agent_workspace}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
