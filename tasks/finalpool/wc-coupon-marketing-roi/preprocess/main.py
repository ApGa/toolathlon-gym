"""Prepare task data for wc-coupon-marketing-roi.

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


def clear_gform(cur):
    """Clear Google Forms data."""
    print("[preprocess] Clearing gform data...")
    cur.execute("DELETE FROM gform.responses")
    cur.execute("DELETE FROM gform.questions")
    cur.execute("DELETE FROM gform.forms")
    print("[preprocess] GForm data cleared.")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        clear_gform(cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
