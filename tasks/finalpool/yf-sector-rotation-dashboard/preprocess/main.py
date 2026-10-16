"""Prepare task data for yf-sector-rotation-dashboard.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""
from argparse import ArgumentParser


def main():
    # No writable schemas to DELETE - read-only data sources
    parser = ArgumentParser()
    parser.add_argument("--agent_workspace", required=False)
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()
    print("[preprocess] HTTP fixture is managed by the environment server.")


if __name__ == "__main__":
    main()
