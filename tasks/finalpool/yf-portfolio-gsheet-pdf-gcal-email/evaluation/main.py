"""Evaluation script for yf-portfolio-gsheet-pdf-gcal-email."""
import os
import argparse, json, os, sys
from grader_helpers import google_sheet_records, close, number, text


DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"), "port": 5432,
    "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
    "user": "eigent", "password": "camel"
}

PASS_COUNT = 0
FAIL_COUNT = 0

def check(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}")
    else:
        FAIL_COUNT += 1
        detail_str = str(detail)[:200] if detail else ""
        print(f"  [FAIL] {name}: {detail_str}")


def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)

def check_submitted_sheet(cur):
    sheets = {}
    for name, columns in [
        ("Holdings", ("symbol", "company", "sector", "current_price", "shares_held", "market_value", "allocation_pct")),
        ("Performance", ("symbol", "purchase_price", "current_price", "return_pct", "status")),
        ("Rebalancing", ("symbol", "current_allocation", "target_allocation", "drift_pct", "action")),
    ]:
        rows = google_sheet_records(cur, "Portfolio Monitor Dashboard", name, columns, check)
        symbols = {str(r["symbol"]).upper() for r in rows}
        check(f"{name} covers the five tracked stocks", len(rows) == 5 and symbols == {"GOOGL", "AMZN", "JPM", "JNJ", "XOM"})
        sheets[name] = rows
    cur.execute("SELECT symbol, data FROM yf.stock_info WHERE symbol IN ('GOOGL', 'AMZN', 'JPM', 'JNJ', 'XOM')")
    prices = {}
    for symbol, data in cur.fetchall():
        data = json.loads(data) if isinstance(data, str) else data
        prices[symbol] = data.get("currentPrice")
    for row in sheets["Holdings"]:
        symbol = str(row["symbol"]).upper()
        shares, value = number(row["shares_held"]), number(row["market_value"])
        price = number(prices.get(symbol))
        check(f"Current holding values for {symbol}", price is not None and close(row["current_price"], price, .02)
              and shares is not None and shares > 0 and value is not None
              and close(value, shares * price, max(2, .006 * price)))
    for row in sheets["Performance"]:
        purchase, current = number(row["purchase_price"]), number(row["current_price"])
        check(f"Return calculation for {row['symbol']}", purchase is not None and purchase > 0
              and current is not None and close(row["return_pct"], (current / purchase - 1) * 100, .03))
    for row in sheets["Rebalancing"]:
        current, target = number(row["current_allocation"]), number(row["target_allocation"])
        check(f"Allocation drift for {row['symbol']}", current is not None and target is not None
              and close(row["drift_pct"], current - target, .15)
              and text(row["action"]) in {"buy", "sell", "hold"})


def run_evaluation(agent_workspace, groundtruth_workspace, launch_time, res_log_file):
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = 0
    FAIL_COUNT = 0

    # Check Python script exists (terminal usage)
    py_files = [f for f in os.listdir(agent_workspace) if f.endswith(".py")]
    check("Python analysis script exists", len(py_files) >= 1, f"found: {py_files}")

    # Database checks
    try:
        conn = get_conn()
        cur = conn.cursor()
        check_submitted_sheet(cur)
        cur.execute("SELECT subject, to_addr FROM email.messages WHERE folder_id = (SELECT id FROM email.folders WHERE name = 'Sent' LIMIT 1) AND subject ILIKE '%portfolio%'")
        email_row = cur.fetchone()
        check("Email with correct subject sent", email_row is not None, "no matching email found")
        if email_row:
            check("Email has recipient", email_row[1] is not None, f"to_addr: {email_row[1]}")
        cur.execute("SELECT summary, start_datetime FROM gcal.events WHERE summary ILIKE '%portfolio%'")
        event_row = cur.fetchone()
        check("Calendar event with correct summary", event_row is not None, "no matching event found")
        cur.execute("SELECT COUNT(*) FROM gsheet.spreadsheets")
        ss_count = cur.fetchone()[0]
        check("Google Sheet created", ss_count >= 1, f"spreadsheet count: {ss_count}")
        cur.execute("SELECT COUNT(*) FROM gsheet.cells")
        cell_count = cur.fetchone()[0]
        check("Sheet has data", cell_count >= 10, f"cell count: {cell_count}")
        # Reverse verification: noise emails should not be in Sent folder
        cur.execute("SELECT COUNT(*) FROM email.messages WHERE folder_id = (SELECT id FROM email.folders WHERE name = 'Sent' LIMIT 1) AND subject ILIKE '%newsletter%'")
        noise_sent = cur.fetchone()[0]
        check("No noise emails in Sent folder", noise_sent == 0, f"found {noise_sent} noise emails in Sent")
        # Reverse verification: noise events should not match task keyword
        cur.execute("SELECT COUNT(*) FROM gcal.events WHERE summary ILIKE '%standup%' OR summary ILIKE '%lunch%'")
        noise_events = cur.fetchone()[0]
        check("Noise events exist (not deleted by agent)", noise_events >= 1, f"noise events: {noise_events}")
        conn.close()
    except Exception as e:
        check("DB checks", False, str(e))

    return FAIL_COUNT == 0, f"Passed {PASS_COUNT}/{PASS_COUNT + FAIL_COUNT} checks"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False, default=".")
    parser.add_argument("--groundtruth_workspace", required=False, default=".")
    parser.add_argument("--launch_time", required=False, default="2026-03-07 10:00:00")
    parser.add_argument("--res_log_file", required=False)
    args = parser.parse_args()

    success, message = run_evaluation(
        args.agent_workspace, args.groundtruth_workspace,
        args.launch_time, args.res_log_file
    )
    print(message)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()