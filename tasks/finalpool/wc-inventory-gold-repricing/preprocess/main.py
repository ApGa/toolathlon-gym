"""Prepare task data for wc-inventory-gold-repricing.

HTTP fixture lifecycle is managed by the environment server (task_config.json).
"""

import argparse


def main():
    # No writable schemas to DELETE - read-only data sources
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent_workspace", required=False, default=".")
    parser.add_argument("--launch_time", required=False)
    args = parser.parse_args()
    print("[preprocess] HTTP fixture is managed by the environment server.")


if __name__ == "__main__":
    main()
