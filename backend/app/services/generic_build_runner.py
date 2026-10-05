"""Bounded subprocess execution with durable queue state and explicit retries."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import BoundedSemaphore

from app.storage.accepted_parts import AcceptedParts

_capacity = BoundedSemaphore(2)


def run_build(job_path: Path, build_id: str):
    with _capacity:
        _run_build(job_path, build_id)


def _run_build(job_path: Path, build_id: str):
    store = AcceptedParts(job_path)
    if not store.claim(build_id):
        return
    try:
        task = store.build(build_id)
        store.get(task["spec_id"])
        interpreter = os.environ.get("GENERIC_GEOMETRY_PYTHON", sys.executable)
        with tempfile.TemporaryFile(mode="w+b") as log:
            process = subprocess.Popen([interpreter, "-m", "app.workers.generic_geometry", str(job_path), build_id],
                                       cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic()+300
            try:
                while process.poll() is None:
                    current = store.build(build_id)
                    if current["status"] == "cancelled" or current["stale"] or time.monotonic() > deadline:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        if current["status"] != "cancelled":
                            store.finish(build_id, error="Specification became stale" if current["stale"] else "Geometry build exceeded the 300 second limit")
                        return
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell()-2000))
                    store.finish(build_id, error=log.read().decode("utf-8", errors="replace") or "Geometry worker failed")
                elif store.build(build_id)["status"] == "running":
                    store.finish(build_id, error="Worker exited without publishing a result")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    except Exception as exc:
        store.finish(build_id, error=str(exc))
