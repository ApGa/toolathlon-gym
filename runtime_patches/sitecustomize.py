"""Runtime compatibility patches for Toolathlon task scripts.

Some upstream task preprocess/evaluation scripts hardcode the original shared
database name (`toolathlon_gym`). The environment server clones a per-session
database and exports it through PGDATABASE/PG_DATABASE; this shim keeps those
scripts isolated without editing every task file.
"""

from __future__ import annotations

import os


def _session_database() -> str | None:
    return os.environ.get("PGDATABASE") or os.environ.get("PG_DATABASE")


def _rewrite_database_name(value):
    session_db = _session_database()
    if session_db and value in {"toolathlon_gym", "toolathlon"}:
        return session_db
    return value


try:
    import psycopg2
except Exception:
    psycopg2 = None

if psycopg2 is not None and not getattr(psycopg2.connect, "_toolathlon_patched", False):
    _original_connect = psycopg2.connect

    def _connect(*args, **kwargs):
        if "dbname" in kwargs:
            kwargs["dbname"] = _rewrite_database_name(kwargs["dbname"])
        if "database" in kwargs:
            kwargs["database"] = _rewrite_database_name(kwargs["database"])
        if args and isinstance(args[0], str):
            dsn = args[0]
            session_db = _session_database()
            if session_db:
                dsn = dsn.replace("dbname=toolathlon_gym", f"dbname={session_db}")
                dsn = dsn.replace("dbname=toolathlon", f"dbname={session_db}")
            args = (dsn, *args[1:])
        return _original_connect(*args, **kwargs)

    _connect._toolathlon_patched = True
    psycopg2.connect = _connect
