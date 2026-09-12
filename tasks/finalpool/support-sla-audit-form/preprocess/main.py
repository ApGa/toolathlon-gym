"""Prepare task data for support-sla-audit-form.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import os
import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": "toolathlon_gym",
    "user": "eigent",
    "password": "camel",
}


def clear_gform(cur):
    """Clear all Google Forms data."""
    print("[preprocess] Clearing Google Forms data...")
    cur.execute("DELETE FROM gform.responses")
    cur.execute("DELETE FROM gform.questions")
    cur.execute("DELETE FROM gform.forms")
    print("[preprocess] Google Forms data cleared.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    # Step 1: Clear Google Forms data
    print("\n" + "=" * 60)
    print("STEP 1: Clear Google Forms Data")
    print("=" * 60)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        clear_gform(cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error clearing gform: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    # Step 2: Set up mock HTTP server
    print("\n" + "=" * 60)
    print("STEP 2: Set Up Mock HTTP Server")
    print("=" * 60)

    print("\n" + "=" * 60)
    print("PREPROCESSING COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    main()
