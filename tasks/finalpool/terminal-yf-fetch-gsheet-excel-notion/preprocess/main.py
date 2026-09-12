"""Prepare task data for terminal-yf-fetch-gsheet-excel-notion.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import glob
import json
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


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def clear_writable(cur):
    print("[preprocess] Clearing gsheet, notion data...")
    # Google Sheets
    cur.execute("DELETE FROM gsheet.cells")
    cur.execute("DELETE FROM gsheet.sheets")
    cur.execute("DELETE FROM gsheet.permissions")
    cur.execute("DELETE FROM gsheet.spreadsheets")

    # Notion
    cur.execute("DELETE FROM notion.blocks")
    cur.execute("DELETE FROM notion.pages")
    cur.execute("DELETE FROM notion.databases")


def inject_noise(cur):
    print("[preprocess] Injecting noise data...")
    # Noise Notion database
    noise_db_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO notion.databases (id, object, title, properties, parent, archived, is_inline)
        VALUES (%s, 'database', %s, '{}', '{"type":"page_id","page_id":"root"}', false, false)
    """, (noise_db_id, json.dumps([{"type": "text", "text": {"content": "Meeting Notes Archive"}}])))

    for note in ["Q4 Planning Session", "Budget Review", "Team Retrospective"]:
        page_id = str(uuid.uuid4())
        cur.execute("""
            INSERT INTO notion.pages (id, object, parent, archived, in_trash, properties)
            VALUES (%s, 'page', %s, false, false, %s)
        """, (page_id, json.dumps({"type": "database_id", "database_id": noise_db_id}),
              json.dumps({"Name": {"title": [{"text": {"content": note}}]}})))

    # Noise Google Sheet
    noise_ss_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO gsheet.spreadsheets (id, title)
        VALUES (%s, 'Q4 Budget Tracker')
    """, (noise_ss_id,))
    cur.execute("""
        INSERT INTO gsheet.sheets (spreadsheet_id, title, "index", row_count, column_count)
        VALUES (%s, 'Sheet1', 0, 100, 10)
    """, (noise_ss_id,))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False, default="2026-03-07 10:00:00")
    args = parser.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    try:
        clear_writable(cur)
        inject_noise(cur)
        conn.commit()
        print("[preprocess] DB setup done.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


    if args.agent_workspace:
        for pattern in ["Market_Analysis_Report.xlsx", "market_*.py", "market_*.json",
                        "stock_*.json", "economic_*.json"]:
            for f in glob.glob(os.path.join(args.agent_workspace, pattern)):
                # This is a supplied task input, not a prior agent output.
                if os.path.basename(f) == "market_params.json":
                    continue
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    main()
