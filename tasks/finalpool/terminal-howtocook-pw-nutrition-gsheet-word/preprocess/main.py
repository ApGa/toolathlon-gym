"""Prepare task data for terminal-howtocook-pw-nutrition-gsheet-word.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
import argparse
import asyncio
import os
import glob as globmod
import uuid
import psycopg2

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"), "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent", "password": "camel",
}


def clear_db():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM gsheet.cells")
        cur.execute("DELETE FROM gsheet.sheets")
        cur.execute("DELETE FROM gsheet.spreadsheets")
        conn.commit()
        print("[preprocess] Cleared gsheet data.")

        # Inject gsheet noise
        ss_id = f"noise-{uuid.uuid4()}"
        cur.execute("INSERT INTO gsheet.spreadsheets (id, title) VALUES (%s, %s)", (ss_id, "Unrelated Budget Notes"))
        cur.execute("INSERT INTO gsheet.sheets (spreadsheet_id, title, index) VALUES (%s, %s, 0) RETURNING id", (ss_id, "Notes"))
        sh_id = cur.fetchone()[0]
        cur.execute("INSERT INTO gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value) VALUES (%s, %s, 1, 1, 'Random budget notes')", (ss_id, sh_id))
        conn.commit()
        print("[preprocess] Injected noise data.")
    except Exception as e:
        conn.rollback()
        print(f"[preprocess] Error clearing gsheet: {e}")
        raise
    finally:
        cur.close()
        conn.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", type=str, required=False)
    parser.add_argument("--launch_time", type=str, required=False)
    args = parser.parse_args()

    clear_db()

    if args.agent_workspace:
        for pattern in ["Wellness_Diet_Plan.docx", "nutrition_calculator.py"]:
            for f in globmod.glob(os.path.join(args.agent_workspace, pattern)):
                os.remove(f)
                print(f"[preprocess] Removed {f}")

    print("[preprocess] Done.")


if __name__ == "__main__":
    asyncio.run(main())
