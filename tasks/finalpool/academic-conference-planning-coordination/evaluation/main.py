#!/usr/bin/env python3
"""Evaluation for a placeholder task that has nothing to grade.

The prompt describes a multi-phase project but names no concrete deliverable
(no file, spreadsheet, form, page, event or e-mail), and the task ships no
input data or reference output to compare against. The previous grader passed
whenever the workspace was non-empty, which the shipped placeholder files
always satisfy, so an episode that did nothing earned full reward. Until the
task is authored with gradeable deliverables, the grader fails closed.
"""

import json
import sys
from argparse import ArgumentParser

MESSAGE = "Placeholder task: no gradeable deliverable is defined, so no reward is awarded"


def main():
    parser = ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--groundtruth_workspace", required=False)
    parser.add_argument("--res_log_file", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()

    if args.res_log_file:
        with open(args.res_log_file, "w") as f:
            json.dump({"total_passed": 0, "total_checks": 1, "accuracy": 0.0}, f)

    print(MESSAGE)
    sys.exit(1)


if __name__ == "__main__":
    main()
