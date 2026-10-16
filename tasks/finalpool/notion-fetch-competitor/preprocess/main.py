"""Prepare task data for notion-fetch-competitor.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse
import asyncio
import os

import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": 5432,
    "dbname": "toolathlon_gym",
    "user": "eigent",
    "password": "camel",
}


def clear_notion(cur):
    """Clear all Notion data."""
    print("[preprocess] Clearing Notion data...")
    cur.execute("DELETE FROM notion.comments")
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")
    cur.execute("DELETE FROM notion.users")
    print("[preprocess] Notion data cleared.")


def ensure_memory_dir(agent_workspace):
    """Ensure memory directory exists in agent workspace."""
    if agent_workspace:
        mem_dir = os.path.join(agent_workspace, "memory")
        os.makedirs(mem_dir, exist_ok=True)
        mem_file = os.path.join(mem_dir, "memory.json")
        if not os.path.exists(mem_file):
            import json
            with open(mem_file, "w") as f:
                json.dump({"notes": []}, f)
        print(f"[preprocess] Memory directory ensured at {mem_dir}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    try:
        clear_notion(cur)
        conn.commit()
        print("[preprocess] Database operations committed.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Database error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


    if args.agent_workspace:
        ensure_memory_dir(args.agent_workspace)

    print("[preprocess] Preprocessing completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
