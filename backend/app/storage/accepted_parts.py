"""Immutable recipe history and persistent build queue, in the source registry DB."""
import hashlib
import json
import time
import uuid

from fastapi import HTTPException
from app.models.accepted_part import AcceptedPartRequest
from app.storage.document_registry import DocumentRegistry


class AcceptedParts:
    def __init__(self, job_path):
        self.registry = DocumentRegistry(job_path)
        self.job_path = job_path

    def connect(self):
        conn = self.registry._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accepted_parts (
                part_key TEXT NOT NULL, version INTEGER NOT NULL, registry_version INTEGER NOT NULL,
                spec_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(part_key, version));
            CREATE TABLE IF NOT EXISTS geometry_builds (
                build_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL, status TEXT NOT NULL,
                started REAL, completed REAL, error TEXT, manifest TEXT);
        """)
        return conn

    def save(self, request: AcceptedPartRequest):
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT version FROM metadata WHERE id=1").fetchone()[0]
                if current != request.expected_registry_version:
                    raise HTTPException(409, "Sources changed. Refresh and review before accepting geometry.")
                evidence_ids = {e.document_id for e in request.evidence}
                for doc_id in evidence_ids:
                    row = conn.execute("SELECT * FROM documents WHERE document_id=?", (doc_id,)).fetchone()
                    association = json.loads(row["association"]) if row and row["association"] else None
                    if not association or (association["part_number"], association["revision"]) != (request.part_number, request.revision):
                        raise HTTPException(422, "Every evidence source must be associated with this part and revision.")
                    if not (self.registry.root / "blobs" / row["sha256"]).is_file():
                        raise HTTPException(409, "Retained source is unavailable.")
                    if doc_id == request.governing_document_id:
                        if association["role"] not in ("finished_drawing", "cad") or association["manufacturing_state"] != "finished":
                            raise HTTPException(422, "Select a finished drawing or finished CAD as the governing source.")
                        if request.base.kind == "step" and not row["path"].lower().endswith((".step", ".stp")):
                            raise HTTPException(422, "STEP import requires a registered STEP source.")
                key = hashlib.sha256(json.dumps([request.part_number, request.revision]).encode()).hexdigest()
                last = conn.execute("SELECT COALESCE(MAX(version),0) FROM accepted_parts WHERE part_key=?", (key,)).fetchone()[0]
                if last != request.expected_version:
                    raise HTTPException(409, "Accepted part changed. Refresh before saving corrections.")
                payload = request.model_dump(exclude={"expected_version", "expected_registry_version"})
                raw = json.dumps(payload, sort_keys=True)
                spec_id = hashlib.sha256(f"{key}:{last+1}:{current}:{raw}".encode()).hexdigest()
                conn.execute("INSERT INTO accepted_parts(part_key,version,registry_version,spec_id,payload) VALUES (?,?,?,?,?)",
                             (key, last+1, current, spec_id, raw))
                return {"part_key": key, "version": last+1, "registry_version": current, "spec_id": spec_id, "payload": payload}
        finally:
            conn.close()

    def list(self):
        conn = self.connect()
        try:
            current = conn.execute("SELECT version FROM metadata WHERE id=1").fetchone()[0]
            rows = conn.execute("SELECT * FROM accepted_parts ORDER BY part_key, version DESC").fetchall()
            latest = {}
            result = []
            for row in rows:
                data = dict(row)
                data["payload"] = json.loads(data["payload"])
                data["stale"] = row["registry_version"] != current or row["part_key"] in latest
                latest[row["part_key"]] = True
                result.append(data)
            return result
        finally:
            conn.close()

    def get(self, spec_id, require_current=True):
        row = next((s for s in self.list() if s["spec_id"] == spec_id), None)
        if row is None:
            raise HTTPException(404, "Accepted part not found")
        if require_current and row["stale"]:
            raise HTTPException(409, "Specification is stale. Review the latest source and correction versions.")
        return row

    def enqueue(self, spec_id):
        self.get(spec_id)
        conn = self.connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                # One active build per spec. Interrupted leases can be retried explicitly.
                row = conn.execute("SELECT * FROM geometry_builds WHERE spec_id=? AND status IN ('queued','running') ORDER BY rowid DESC", (spec_id,)).fetchone()
                if row and (row["status"] == "queued" or (row["started"] or 0) > time.time()-360):
                    return dict(row)
                if row:
                    conn.execute("UPDATE geometry_builds SET status='failed', error='Build interrupted or timed out' WHERE build_id=?", (row["build_id"],))
                build_id = uuid.uuid4().hex
                conn.execute("INSERT INTO geometry_builds(build_id,spec_id,status) VALUES (?,?,'queued')", (build_id, spec_id))
            return self.build(build_id)
        finally:
            conn.close()

    def build(self, build_id):
        conn = self.connect()
        try:
            row = conn.execute("SELECT * FROM geometry_builds WHERE build_id=?", (build_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Build not found")
            data = dict(row)
            data["manifest"] = json.loads(data["manifest"]) if data["manifest"] else None
            data["stale"] = self.get(data["spec_id"], False)["stale"]
            return data
        finally:
            conn.close()

    def builds_for(self, spec_id):
        self.get(spec_id, require_current=False)
        conn = self.connect()
        try:
            ids = [r[0] for r in conn.execute("SELECT build_id FROM geometry_builds WHERE spec_id=? ORDER BY rowid DESC", (spec_id,))]
        finally:
            conn.close()
        return [self.build(build_id) for build_id in ids]

    def claim(self, build_id):
        conn = self.connect()
        try:
            with conn:
                return conn.execute("UPDATE geometry_builds SET status='running', started=? WHERE build_id=? AND status='queued'",
                                    (time.time(), build_id)).rowcount == 1
        finally:
            conn.close()

    def finish(self, build_id, manifest=None, error=None):
        conn = self.connect()
        try:
            with conn:
                conn.execute("UPDATE geometry_builds SET status=?, completed=?, manifest=?, error=? WHERE build_id=? AND status='running'",
                             ("failed" if error else "complete", time.time(), json.dumps(manifest) if manifest else None, error, build_id))
        finally:
            conn.close()

    def cancel(self, build_id):
        self.build(build_id)
        conn = self.connect()
        try:
            with conn:
                conn.execute("UPDATE geometry_builds SET status='cancelled',completed=? WHERE build_id=? AND status IN ('queued','running')", (time.time(), build_id))
        finally:
            conn.close()
