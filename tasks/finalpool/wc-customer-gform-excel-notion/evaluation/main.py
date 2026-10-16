"""Evaluation script for wc-customer-gform-excel-notion."""
import argparse
import json
import os
import sys
import openpyxl
from grader_helpers import records, close, text


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

def check_customer_data(wb, customers):
    segments = records(wb, "Customer_Segments", ("segment", "customer_count", "total_revenue", "avg_spend", "avg_orders"), check)
    top = records(wb, "Top_Customers", ("customer_name", "email", "total_spent", "order_count", "segment"), check)
    strategies = records(wb, "Segment_Strategy", ("segment", "engagement_strategy", "retention_risk", "target_action"), check)
    def tier(spend):
        return "VIP" if float(spend) > 500 else "Regular" if float(spend) >= 100 else "New"
    totals = {}
    for name, email, spend, orders in customers:
        values = totals.setdefault(tier(spend), [0, 0.0, 0])
        values[0] += 1
        values[1] += float(spend)
        values[2] += orders
    by_segment = {text(r["segment"]): r for r in segments}
    check("All customer segments represented once", len(segments) == len(totals)
          and set(by_segment) == {text(t) for t in totals})
    for segment, (count, revenue, orders) in totals.items():
        row = by_segment.get(text(segment), {})
        check(f"{segment} statistics match store data", all(close(row.get(key), value, tol)
              for key, value, tol in [("customer_count", count, 0), ("total_revenue", revenue, .02),
                                      ("avg_spend", revenue / count, .02), ("avg_orders", orders / count, .06)]))
    check("Segments sorted by revenue", [text(r["segment"]) for r in segments] ==
          [text(t) for t in sorted(totals, key=lambda t: totals[t][1], reverse=True)])
    # Ties in spending may be reported in any order.
    expected = sorted(customers, key=lambda r: float(r[2]), reverse=True)[:10]
    by_email = {text(r[1]): r for r in customers}
    threshold = float(expected[-1][2]) if expected else 0
    emails = [text(r["email"]) for r in top]
    check("Top customers contains ten distinct store customers", len(top) == len(expected)
          and len(set(emails)) == len(top) and all(e in by_email for e in emails)
          and {text(r[1]) for r in customers if float(r[2]) > threshold}.issubset(emails))
    previous = float("inf")
    for row in top:
        customer = by_email.get(text(row["email"]))
        if customer is None:
            continue
        name, email, spend, orders = customer
        check(f"Top customer {email} values", text(row["customer_name"]) == text(name)
              and close(row["total_spent"], spend, .02) and close(row["order_count"], orders, 0)
              and text(row["segment"]) == text(tier(spend)) and threshold <= float(spend) <= previous)
        previous = float(spend)
    check("Strategies cover every segment", {text(r["segment"]) for r in strategies} == {text(t) for t in totals}
          and all(all(text(r[k]) for k in ("engagement_strategy", "retention_risk", "target_action")) for r in strategies))


def run_evaluation(agent_workspace, groundtruth_workspace, launch_time, res_log_file):
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = FAIL_COUNT = 0
    path = os.path.join(agent_workspace, "Customer_Insights_Report.xlsx")
    check("Customer_Insights_Report.xlsx exists", os.path.isfile(path))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trim(concat_ws(' ', first_name, last_name)), email, total_spent, orders_count FROM wc.customers")
            customers = cur.fetchall()
            if os.path.isfile(path):
                wb = openpyxl.load_workbook(path, data_only=True)
                try:
                    check_customer_data(wb, customers)
                finally:
                    wb.close()
            cur.execute("SELECT id FROM gform.forms WHERE lower(title) = 'customer experience survey'")
            forms = cur.fetchall()
            check("Customer Experience Survey created", bool(forms))
            if forms:
                cur.execute("SELECT COUNT(*) FROM gform.questions WHERE form_id = %s", (forms[0][0],))
                check("Survey contains four questions", cur.fetchone()[0] >= 4)
            cur.execute("SELECT COUNT(*) FROM notion.pages WHERE archived = false AND properties::text ILIKE %s", ("%Customer Intelligence Hub%",))
            check("Customer Intelligence Hub created", cur.fetchone()[0] >= 1)
    finally:
        conn.close()
    check("customer_segmenter.py exists", os.path.isfile(os.path.join(agent_workspace, "customer_segmenter.py")))
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