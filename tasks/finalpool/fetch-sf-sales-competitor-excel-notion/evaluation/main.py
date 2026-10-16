"""Evaluation script for fetch-sf-sales-competitor-excel-notion."""
import argparse
import json
import os
import sys
import openpyxl
import tarfile
from pathlib import Path
from grader_helpers import close, text, records


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

def load_benchmarks():
    # Read the exact fixture served by this task, rather than the unrelated
    # example workbook. This also works in per-episode task copies.
    archive = Path(__file__).resolve().parents[1] / "files/mock_pages.tar.gz"
    with tarfile.open(archive) as bundle:
        member = next(m for m in bundle.getmembers() if m.name.endswith("api/data.json"))
        data = json.load(bundle.extractfile(member))
    return {row["department"]: float(row["industry_avg"]) for row in data["benchmarks"]}


def check_comparison(wb, salaries, benchmarks):
    rows = records(wb, "Data_Analysis", ("department", "internal_avg_salary", "industry_avg", "gap"), check)
    by_department = {text(r["department"]): r for r in rows}
    check("All benchmark departments represented once", len(rows) == len(benchmarks)
          and set(by_department) == {text(d) for d in benchmarks})
    for department, benchmark in benchmarks.items():
        row = by_department.get(text(department), {})
        internal = salaries.get(department)
        check(f"{department} comparison values", internal is not None
              and close(row.get("internal_avg_salary"), internal, .02)
              and close(row.get("industry_avg"), benchmark, .02)
              and close(row.get("gap"), float(internal or 0) - benchmark, .02))
    dimensions = [text(r["department"]) for r in rows]
    check("Departments sorted alphabetically", dimensions == sorted(dimensions))
    metrics = records(wb, "Metrics", ("metric", "value"), check)
    check("Metrics has meaningful summary rows", len(metrics) >= 4
          and all(text(r["metric"]) and r["value"] is not None for r in metrics))
    # Recommendations are free-form; do not impose the old sales-region taxonomy.
    sheet = next((sheet for sheet in wb if text(sheet.title) == "recommendations"), None)
    check("Recommendations sheet exists", sheet is not None)
    if sheet is not None:
        data = [row for row in sheet.iter_rows(min_row=2, values_only=True) if any(text(v) for v in row)]
        check("Recommendations contains actionable items", len(data) >= 2
              and all(sum(bool(text(v)) for v in row) >= 2 for row in data))


def run_evaluation(agent_workspace, groundtruth_workspace, launch_time, res_log_file):
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = FAIL_COUNT = 0
    path = os.path.join(agent_workspace, "Sales_Competitor_Report.xlsx")
    check("Sales_Competitor_Report.xlsx exists", os.path.isfile(path))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT "DEPARTMENT", AVG("SALARY") FROM sf_data."HR_ANALYTICS__PUBLIC__EMPLOYEES" GROUP BY "DEPARTMENT"')
            salaries = dict(cur.fetchall())
            if os.path.isfile(path):
                wb = openpyxl.load_workbook(path, data_only=True)
                try:
                    check_comparison(wb, salaries, load_benchmarks())
                finally:
                    wb.close()
            cur.execute("SELECT COUNT(*) FROM notion.pages WHERE properties::text ILIKE %s AND archived = false", ("%Sf Competitor Dashboard%",))
            check("Notion dashboard created", cur.fetchone()[0] >= 1)
    finally:
        conn.close()
    check("sf_competitor_processor.py exists", os.path.isfile(os.path.join(agent_workspace, "sf_competitor_processor.py")))
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
