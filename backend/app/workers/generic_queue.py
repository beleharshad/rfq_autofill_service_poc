"""Restartable queue dispatcher: python -m app.workers.generic_queue [--once]."""
import argparse
import time
from pathlib import Path
from app.storage.paths import jobs_root
from app.storage.accepted_parts import AcceptedParts
from app.services.generic_build_runner import run_build


def drain(root: Path):
    for database in sorted(root.glob("*/source_registry/registry.sqlite3")):
        store = AcceptedParts(database.parent.parent)
        conn = store.connect()
        try:
            with conn:
                # Hard-killed dispatchers leave running leases. Their results stay unusable.
                conn.execute("UPDATE geometry_builds SET status='failed',error='Interrupted build: retry required' WHERE status='running' AND started<?", (time.time()-360,))
            queued = conn.execute("SELECT build_id FROM geometry_builds WHERE status='queued' ORDER BY rowid").fetchall()
        finally:
            conn.close()
        for row in queued:
            run_build(store.job_path, row["build_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        drain(jobs_root())
        if args.once:
            break
        time.sleep(2)
