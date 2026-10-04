"""Per-job source snapshots and transactional, versioned associations."""
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi import HTTPException
from app.models.document_registry import AssociationRequest


class DocumentRegistry:
    def __init__(self, job_path: Path):
        self.job_path = job_path
        self.root = job_path / "source_registry"
        self.db = self.root / "registry.sqlite3"

    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
            INSERT OR IGNORE INTO metadata VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, original_name TEXT NOT NULL,
                association TEXT, UNIQUE(path, sha256));
            CREATE TABLE IF NOT EXISTS history (
                version INTEGER PRIMARY KEY, document_id TEXT NOT NULL,
                association TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)
        return conn

    def register(self, path: Path, relative_path: str, original_name: str | None = None):
        """Copy first, then hash/register the exact retained bytes. Never overwrite a blob."""
        blobs = self.root / "blobs"
        blobs.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        fd, temp = tempfile.mkstemp(dir=blobs)
        try:
            with os.fdopen(fd, "wb") as target, path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    target.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            sha = digest.hexdigest()
            # A hard link publishes a completed file atomically without replacement.
            try:
                os.link(temp, blobs / sha)
            except FileExistsError:
                pass
        finally:
            Path(temp).unlink(missing_ok=True)
        doc_id = hashlib.sha256(f"{self.job_path.name}:{relative_path}:{sha}".encode()).hexdigest()
        conn = self._connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute("INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, NULL)",
                                      (doc_id, relative_path, sha, size, original_name or path.name))
                if cursor.rowcount:
                    conn.execute("UPDATE metadata SET version=version+1 WHERE id=1")
        finally:
            conn.close()
        return doc_id

    def read(self):
        if not self.db.exists():
            return {"version": 0, "documents": []}
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            version = conn.execute("SELECT version FROM metadata WHERE id=1").fetchone()[0]
            documents = []
            for row in conn.execute("SELECT * FROM documents ORDER BY path, document_id"):
                item = dict(row)
                item["association"] = json.loads(item["association"]) if item["association"] else None
                documents.append(item)
            return {"version": version, "documents": documents}
        finally:
            conn.close()

    def associate(self, document_id: str, request: AssociationRequest):
        conn = self._connect()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                version = conn.execute("SELECT version FROM metadata WHERE id=1").fetchone()[0]
                if version != request.expected_version:
                    raise HTTPException(409, "Document registry changed. Refresh before saving your association.")
                if not conn.execute("SELECT 1 FROM documents WHERE document_id=?", (document_id,)).fetchone():
                    raise HTTPException(404, "Document not registered")
                data = json.dumps(request.model_dump(exclude={"expected_version"}), sort_keys=True)
                conn.execute("UPDATE documents SET association=? WHERE document_id=?", (data, document_id))
                conn.execute("UPDATE metadata SET version=version+1 WHERE id=1")
                conn.execute("INSERT INTO history(version, document_id, association) VALUES (?, ?, ?)",
                             (version + 1, document_id, data))
            return {"version": version + 1, "document_id": document_id}
        finally:
            conn.close()

    def history(self, document_id: str):
        if not self.db.exists():
            raise HTTPException(404, "Document not registered")
        conn = self._connect()
        try:
            if not conn.execute("SELECT 1 FROM documents WHERE document_id=?", (document_id,)).fetchone():
                raise HTTPException(404, "Document not registered")
            return [{**dict(row), "association": json.loads(row["association"])} for row in conn.execute(
                "SELECT version, association, created_at FROM history WHERE document_id=? ORDER BY version", (document_id,))]
        finally:
            conn.close()
