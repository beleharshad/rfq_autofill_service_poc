"""Bounded isolated automatic processing; the database survives API restarts."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import BoundedSemaphore
from app.storage.automatic_runs import AutomaticRuns
from app.storage.accepted_parts import AcceptedParts

_capacity = BoundedSemaphore(1)


def run_automatic(job_path, run_id):
    with _capacity:
        runs = AutomaticRuns(job_path)
        if not runs.claim(run_id): return
        process = None
        try:
            with tempfile.TemporaryFile() as log:
                process = subprocess.Popen([os.getenv('GENERIC_GEOMETRY_PYTHON',sys.executable),
                    '-m','app.workers.automatic',str(job_path),run_id],
                    cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic()+7200
                while process.poll() is None:
                    if runs.get(run_id)['status']=='cancelled': break
                    if time.monotonic()>deadline: raise RuntimeError('Automatic run exceeded two hours; split the batch')
                    runs.update(run_id)
                    try: process.wait(timeout=1)
                    except subprocess.TimeoutExpired: pass
                if process.poll() is not None and runs.get(run_id)['status']=='running':
                    raise RuntimeError('Automatic worker exited without a result; verify geometry worker dependencies')
        except Exception as exc:
            runs.update(run_id,status='failed',error=str(exc))
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
            if runs.get(run_id)['status'] in ('cancelled','failed'):
                store = AcceptedParts(job_path)
                for item in runs.get(run_id)['result']['items']:
                    if item.get('build_id'): store.cancel(item['build_id'])
