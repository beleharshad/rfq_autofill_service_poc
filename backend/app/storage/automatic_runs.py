"""Durable, idempotent job-wide automatic runs with optimistic source snapshots."""
import json
import time
import uuid
from fastapi import HTTPException
from app.storage.document_registry import DocumentRegistry


class AutomaticRuns:
    def __init__(self, job_path):
        self.job_path = job_path
        self.registry = DocumentRegistry(job_path)

    def connect(self):
        conn = self.registry._connect()
        conn.execute('''CREATE TABLE IF NOT EXISTS automatic_runs (
            run_id TEXT PRIMARY KEY, registry_version INTEGER NOT NULL, status TEXT NOT NULL,
            heartbeat REAL, result TEXT NOT NULL, error TEXT)''')
        conn.commit()
        return conn

    def enqueue(self, retry=False):
        conn = self.connect()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                version = conn.execute('SELECT version FROM metadata WHERE id=1').fetchone()[0]
                active = conn.execute("SELECT * FROM automatic_runs WHERE status IN ('queued','running') ORDER BY rowid DESC LIMIT 1").fetchone()
                if active:
                    return self._decode(active)
                last = conn.execute('SELECT * FROM automatic_runs ORDER BY rowid DESC LIMIT 1').fetchone()
                if last and last['registry_version'] == version and not retry:
                    return self._decode(last)
                run_id = uuid.uuid4().hex
                conn.execute("INSERT INTO automatic_runs VALUES (?,?,'queued',NULL,?,NULL)",
                             (run_id, version, json.dumps({'items': [], 'phase': 'queued'})))
            return self.get(run_id)
        finally:
            conn.close()

    @staticmethod
    def _decode(row):
        data = dict(row)
        data['result'] = json.loads(data['result'])
        return data

    def get(self, run_id=None):
        conn = self.connect()
        try:
            row = conn.execute('SELECT * FROM automatic_runs WHERE run_id=?', (run_id,)).fetchone() if run_id else conn.execute('SELECT * FROM automatic_runs ORDER BY rowid DESC LIMIT 1').fetchone()
            if row is None:
                if run_id: raise HTTPException(404, 'Automatic run not found')
                return None
            data = self._decode(row)
            data['stale'] = data['registry_version'] != self.registry.read()['version']
            return data
        finally:
            conn.close()

    def claim(self, run_id):
        conn = self.connect()
        try:
            with conn:
                return conn.execute("UPDATE automatic_runs SET status='running',heartbeat=? WHERE run_id=? AND status='queued'",
                                    (time.time(), run_id)).rowcount == 1
        finally: conn.close()

    def update(self, run_id, result=None, status=None, error=None, registry_version=None):
        conn = self.connect()
        try:
            with conn:
                conn.execute('''UPDATE automatic_runs SET heartbeat=?, result=COALESCE(?,result),
                    status=COALESCE(?,status),error=?,registry_version=COALESCE(?,registry_version)
                    WHERE run_id=? AND status='running' ''',
                    (time.time(), json.dumps(result) if result is not None else None, status, error, registry_version, run_id))
        finally: conn.close()

    def ensure_current(self, run_id, version):
        run = self.get(run_id)
        if run['status'] != 'running': raise RuntimeError('Automatic run was stopped')
        if self.registry.read()['version'] != version:
            raise RuntimeError('Source registry changed during automatic processing; retry against latest sources')

    def cancel(self, run_id):
        self.get(run_id)
        conn = self.connect()
        try:
            with conn:
                conn.execute("UPDATE automatic_runs SET status='cancelled' WHERE run_id=? AND status IN ('queued','running')", (run_id,))
        finally: conn.close()
