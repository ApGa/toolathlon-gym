"""Evaluation script for canvas-scholarly-curriculum-excel-notion."""
import argparse
import json
import os
import sys
import openpyxl
from grader_helpers import records, close, number, text


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

def check_curriculum(wb, courses, papers):
    course_rows = records(wb, "Current_Courses", ("course_name", "course_code", "enrollment_count", "avg_score"), check)
    research = records(wb, "Research_Trends", ("paper_title", "topic_area", "year", "citations", "relevance_to_curriculum"), check)
    gaps = records(wb, "Gap_Analysis", ("metric", "value"), check)
    by_code = {text(r["course_code"]): r for r in course_rows}
    check("All offered courses represented once", len(course_rows) == len(courses)
          and set(by_code) == {text(c[1]) for c in courses})
    for name, code, enrollment, score in courses:
        row = by_code.get(text(code), {})
        check(f"Course {code} values", text(row.get("course_name")) == text(name)
              and close(row.get("enrollment_count"), enrollment, 0)
              and (close(row.get("avg_score"), score, .06) if score is not None
                   else row.get("avg_score") in (None, "", "N/A")))
    names = [text(r["course_name"]) for r in course_rows]
    check("Courses sorted by name", names == sorted(names))
    paper_lookup = {}
    for title, year, citations in papers:
        paper_lookup.setdefault(text(title), []).append((year, citations))
    titles = [text(r["paper_title"]) for r in research]
    check("Research contains distinct available papers", bool(research)
          and len(titles) == len(set(titles)) and all(t in paper_lookup for t in titles))
    for row in research:
        expected = paper_lookup.get(text(row["paper_title"]))
        if expected is None:
            continue
        check(f"Paper metadata: {row['paper_title']}",
              any(close(row["year"], year, 0) and close(row["citations"], citations, 0)
                  for year, citations in expected) and bool(text(row["topic_area"]))
              and text(row["relevance_to_curriculum"]) in {"high", "medium", "low"})
    citations = [number(r["citations"]) for r in research]
    check("Research sorted by citations", all(c is not None for c in citations)
          and citations == sorted(citations, key=lambda c: c if c is not None else -1, reverse=True))
    metrics = {text(r["metric"]): r["value"] for r in gaps}
    for name, expected in [("total_courses", len(courses)), ("papers_reviewed", len(research)),
                           ("high_relevance_papers", sum(text(r["relevance_to_curriculum"]) == "high" for r in research))]:
        check(f"Gap metric {name}", close(metrics.get(name), expected, 0))
    # Coverage and topical relevance are judgments; validate the stated range
    # and required output rather than insisting on placeholder prose.
    coverage = number(metrics.get("curriculum_coverage_pct"))
    check("Curriculum coverage is a percentage", coverage is not None and 0 <= coverage <= 100)
    check("Top gap area provided", bool(text(metrics.get("top_gap_area"))))


def run_evaluation(agent_workspace, groundtruth_workspace, launch_time, res_log_file):
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = FAIL_COUNT = 0
    path = os.path.join(agent_workspace, "Curriculum_Review_Report.xlsx")
    check("Curriculum_Review_Report.xlsx exists", os.path.isfile(path))
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.name, c.course_code, c.total_students, AVG(s.score)
                FROM canvas.courses c LEFT JOIN canvas.assignments a ON a.course_id = c.id
                LEFT JOIN canvas.submissions s ON s.assignment_id = a.id
                WHERE c.workflow_state = 'available'
                GROUP BY c.id, c.name, c.course_code, c.total_students ORDER BY c.name
            """)
            courses = cur.fetchall()
            cur.execute("""
                SELECT title, pub_year, citation_count FROM scholarly.scholar_papers
                UNION ALL SELECT title, EXTRACT(YEAR FROM published), 0 FROM scholarly.arxiv_papers
            """)
            papers = cur.fetchall()
            if os.path.isfile(path):
                wb = openpyxl.load_workbook(path, data_only=True)
                try:
                    check_curriculum(wb, courses, papers)
                finally:
                    wb.close()
            cur.execute("SELECT COUNT(*) FROM notion.pages WHERE archived = false AND properties::text ILIKE %s", ("%Curriculum Innovation Tracker%",))
            check("Curriculum Innovation Tracker created", cur.fetchone()[0] >= 1)
    finally:
        conn.close()
    check("curriculum_reviewer.py exists", os.path.isfile(os.path.join(agent_workspace, "curriculum_reviewer.py")))
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