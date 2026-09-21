"""Validate submitted data against the episode's sources, not example workbooks."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import openpyxl
from grader_helpers import close, number, records, rich_text, text

DB_CONFIG = {"host": os.environ.get("PGHOST", "localhost"), "port": 5432,
             "dbname": os.environ.get("PGDATABASE", "toolathlon_gym"),
             "user": "eigent", "password": "camel"}
PASS_COUNT = FAIL_COUNT = 0


def check(name, condition, detail=""):
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  [PASS] {name}")
    else:
        FAIL_COUNT += 1
        print(f"  [FAIL] {name}: {str(detail)[:200]}")


def get_conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)


def check_artifacts(workspace, script, json_files):
    check(f"{script} exists", (workspace / script).is_file())
    for name in json_files:
        try:
            value = json.loads((workspace / name).read_text())
            check(f"{name} contains data", bool(value))
        except (OSError, ValueError) as error:
            check(f"{name} is valid JSON", False, error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", default=".")
    parser.add_argument("--groundtruth_workspace", default=".")
    parser.add_argument("--launch_time", default="2026-03-07 10:00:00")
    parser.add_argument("--res_log_file")
    args = parser.parse_args()
    success, message = run_evaluation(args.agent_workspace, args.groundtruth_workspace,
                                      args.launch_time, args.res_log_file)
    print(message)
    sys.exit(0 if success else 1)


def available_papers(cursor):
    cursor.execute("""
        SELECT id::text, title, authors, pub_year, citation_count, NULL::text
        FROM scholarly.scholar_papers
        UNION ALL SELECT id, title, authors, EXTRACT(YEAR FROM published)::int, 0, primary_category
        FROM scholarly.arxiv_papers
        UNION ALL SELECT id, title, authors, EXTRACT(YEAR FROM published)::int, 0, primary_category
        FROM arxiv.papers
    """)
    return cursor.fetchall()


def author_names(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [text(value)]
    return [text(item.get("name", "") if isinstance(item, dict) else item) for item in (value or [])]


def check_research(book, papers):
    catalog = records(book, "Paper_Catalog", ("paper_id", "title", "authors", "year", "category", "citation_count"), check)
    methods = records(book, "Method_Comparison", ("method_name", "paper_source", "key_innovation", "benchmark_result", "applicability"), check)
    gaps = records(book, "Research_Gaps", ("gap_area", "current_state", "opportunity", "priority"), check)
    lookup = {}
    for row in papers:
        lookup.setdefault(text(row[1]), []).append(row)
    titles = [text(row["title"]) for row in catalog]
    check("At least five distinct available papers", len(titles) >= 5 and len(titles) == len(set(titles))
          and all(title in lookup for title in titles))
    for row in catalog:
        matches = lookup.get(text(row["title"]), [])
        identifier = str(row["paper_id"]).removeprefix("https://arxiv.org/abs/").removeprefix("arxiv:")
        authors = text(row["authors"])
        check(f"Retrieved metadata: {row['title']}", any(
            (identifier == str(pid) if category else bool(text(row["paper_id"])))
            and close(row["year"], year, 0)
            and close(row["citation_count"], citations, 0)
            and all(name in authors for name in author_names(source_authors))
            and (text(category) in re.split(r"[\s,;\[\]'\"]+", text(row["category"]))
                 if category else bool(text(row["category"])))
            for pid, _, source_authors, year, citations, category in matches))
    citations = [number(row["citation_count"]) for row in catalog]
    check("Papers sorted by citation count", all(value is not None for value in citations)
          and citations == sorted(citations, key=lambda value: value if value is not None else -1, reverse=True))
    identifiers = [text(row["paper_id"]) for row in catalog]
    check("Method comparison has content", bool(methods))
    for row in methods:
        source = text(row["paper_source"])
        check("Method refers to selected paper and has findings", any(value in source for value in titles + identifiers)
              and all(text(row[key]) for key in ("method_name", "key_innovation", "benchmark_result"))
              and text(row["applicability"]) in {"high", "medium", "low"})
    check("At least four described research gaps", len(gaps) >= 4 and all(
        all(text(row[key]) for key in ("gap_area", "current_state", "opportunity"))
        and text(row["priority"]) in {"critical", "important", "nice-to-have"} for row in gaps))


def run_evaluation(agent_workspace, groundtruth_workspace, launch_time, res_log_file):
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = FAIL_COUNT = 0
    workspace = Path(agent_workspace)
    path = workspace / "Research_Knowledge_Base.xlsx"
    check(path.name + " exists", path.is_file())
    conn = get_conn()
    try:
        with conn.cursor() as cursor:
            papers = available_papers(cursor)
            if path.is_file():
                book = openpyxl.load_workbook(path, data_only=True)
                try:
                    check_research(book, papers)
                finally:
                    book.close()
            cursor.execute("SELECT properties FROM notion.pages WHERE archived = false")
            check("LLM Research Hub created", any(
                text(rich_text(properties.get("title", {}))) == "llm research hub"
                for (properties,) in cursor.fetchall()))
    finally:
        conn.close()
    check_artifacts(workspace, "research_synthesizer.py", ["papers_metadata.json", "paper_contents.json", "research_synthesis.json"])
    return FAIL_COUNT == 0, f"Passed {PASS_COUNT}/{PASS_COUNT + FAIL_COUNT} checks"


if __name__ == "__main__":
    main()
