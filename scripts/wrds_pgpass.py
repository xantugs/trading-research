"""Save your WRDS login for PostgreSQL clients (run this yourself; nothing is printed or logged).

    .venv/Scripts/python -m scripts.wrds_pgpass

It writes `<host>:9737:wrds:<user>:<password>` to %APPDATA%/postgresql/pgpass.conf (Windows) or
~/.pgpass, which libpq reads on every connection, then tests one connection. Approve the Duo
push if WRDS sends one.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from orb_research.crsp import DBNAME, HOST, PORT, connect


def pgpass_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "postgresql" / "pgpass.conf"
    return Path.home() / ".pgpass"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:")


def main() -> None:
    user = input("WRDS username: ").strip()
    password = getpass.getpass("WRDS password (not shown): ")
    path = pgpass_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{HOST}:{PORT}:{DBNAME}:{_escape(user)}:"
    lines = [ln for ln in path.read_text().splitlines() if not ln.startswith(prefix)] if path.exists() else []
    lines.append(prefix + _escape(password))
    path.write_text("\n".join(lines) + "\n")
    if sys.platform != "win32":
        path.chmod(0o600)
    print(f"saved to {path}; testing the connection (approve Duo if asked) ...")
    conn = connect(user)
    conn.close()
    print(f"OK. Use --user {user} with scripts.jkx_download_crsp")


if __name__ == "__main__":
    main()
