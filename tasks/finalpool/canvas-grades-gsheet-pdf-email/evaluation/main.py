"""Evaluation script for canvas-grades-gsheet-pdf-email."""
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
    rows = google_sheet_records(cur, "Department Grade Dashboard", "Grade_Distribution",
        ("course_name", "a_count", "b_count", "c_count", "d_count", "f_count", "total_students", "pass_rate_pct", "course_avg"), check)
    summary = google_sheet_records(cur, "Department Grade Dashboard", "Department_Summary", ("metric", "value"), check)
    cur.execute("SELECT name FROM canvas.courses")
    names = {text(r[0]) for r in cur.fetchall()}
    check("Grade distribution covers all courses", len(rows) == len(names)
          and {text(r["course_name"]) for r in rows} == names)
    for row in rows:
        counts = [number(row[k]) for k in ("a_count", "b_count", "c_count", "d_count", "f_count")]
        total, avg = number(row["total_students"]), number(row["course_avg"])
        valid = all(c is not None and c >= 0 and c.is_integer() for c in counts)
        valid = valid and total is not None and total > 0 and close(sum(counts), total, 0)
        valid = valid and close(row["pass_rate_pct"], 100 * sum(counts[:3]) / total, .06)
        valid = valid and avg is not None and 0 <= avg <= 100
        check(f"Grade distribution arithmetic: {row['course_name']}", valid)
    metrics = {text(r["metric"]): r["value"] for r in summary}
    required = {"total_courses", "total_students", "overall_pass_rate", "overall_avg_grade", "highest_avg_course", "lowest_avg_course"}
    check("Department summary contains all required metrics", required.issubset(metrics))
    check("Department course count", close(metrics.get("total_courses"), len(names), 0))


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
        cur.execute("SELECT subject, to_addr FROM email.messages WHERE folder_id = (SELECT id FROM email.folders WHERE name = 'Sent' LIMIT 1) AND subject ILIKE '%grade%'")
        email_row = cur.fetchone()
        check("Email with correct subject sent", email_row is not None, "no matching email found")
        if email_row:
            check("Email has recipient", email_row[1] is not None, f"to_addr: {email_row[1]}")
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