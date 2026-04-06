"""Job metadata storage."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from app.models.job import JobResponse, JobStatus
from app.storage.paths import jobs_db_path, legacy_jobs_db_paths


class JobStorage:
    """Handles job metadata storage in SQLite."""
    
    def __init__(self, db_path: str | Path | None = None):
        """Initialize job storage.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path).resolve() if db_path is not None else jobs_db_path()
        self.read_db_paths = [self.db_path, *legacy_jobs_db_paths()]
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self, db_path: Path | None = None) -> sqlite3.Connection:
        return sqlite3.connect(db_path or self.db_path)

    @staticmethod
    def _parse_datetime(dt_str: str) -> datetime:
        if isinstance(dt_str, datetime):
            return dt_str
        try:
            return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        except ValueError:
            try:
                return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return datetime.now()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobResponse:
        mode = None
        try:
            mode = row['mode']
        except (KeyError, IndexError):
            pass

        return JobResponse(
            job_id=row['job_id'],
            name=row['name'],
            description=row['description'],
            status=row['status'],
            mode=mode,
            input_files=[],
            output_files=[],
            created_at=JobStorage._parse_datetime(row['created_at']),
            updated_at=JobStorage._parse_datetime(row['updated_at'])
        )

    def _candidate_db_paths(self) -> List[Path]:
        return [path for path in self.read_db_paths if path.exists()]

    def _find_job_db_path(self, job_id: str) -> Optional[Path]:
        for db_path in self._candidate_db_paths():
            conn = self._connect(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
                if cursor.fetchone():
                    return db_path
            finally:
                conn.close()
        return None
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                status TEXT NOT NULL,
                mode TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Add mode column to existing tables if it doesn't exist
        try:
            cursor.execute("ALTER TABLE jobs ADD COLUMN mode TEXT")
        except sqlite3.OperationalError:
            # Column already exists, ignore
            pass
        
        conn.commit()
        conn.close()
    
    def create_job(
        self, 
        job_id: str, 
        name: Optional[str] = None, 
        description: Optional[str] = None,
        mode: Optional[str] = None
    ) -> None:
        """Create a new job.
        
        Args:
            job_id: Job identifier
            name: Optional job name
            description: Optional job description
            mode: Optional job mode (assisted_manual or auto_convert)
        """
        conn = self._connect()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO jobs (job_id, name, description, status, mode)
            VALUES (?, ?, ?, ?, ?)
        """, (job_id, name, description, JobStatus.CREATED, mode))
        
        conn.commit()
        conn.close()
    
    def get_job(self, job_id: str) -> Optional[JobResponse]:
        """Get job by ID.
        
        Args:
            job_id: Job identifier
            
        Returns:
            JobResponse or None if not found
        """
        for db_path in self._candidate_db_paths():
            conn = self._connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            conn.close()

            if row:
                return self._row_to_job(row)

        return None
    
    def update_job_status(self, job_id: str, status: str) -> None:
        """Update job status.
        
        Args:
            job_id: Job identifier
            status: New status
        """
        target_db = self._find_job_db_path(job_id) or self.db_path
        conn = self._connect(target_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE jobs
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
        """, (status, job_id))
        
        conn.commit()
        conn.close()
    
    def list_jobs(self) -> List[JobResponse]:
        """List all jobs.
        
        Returns:
            List of JobResponse objects
        """
        jobs_by_id: dict[str, JobResponse] = {}

        for db_path in self._candidate_db_paths():
            conn = self._connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
            rows = cursor.fetchall()
            conn.close()

            for row in rows:
                job = self._row_to_job(row)
                existing = jobs_by_id.get(job.job_id)
                if existing is None or job.created_at > existing.created_at:
                    jobs_by_id[job.job_id] = job

        return sorted(jobs_by_id.values(), key=lambda job: job.created_at, reverse=True)
    
    def update_job_mode(self, job_id: str, mode: str) -> None:
        """Update job mode.
        
        Args:
            job_id: Job identifier
            mode: Job mode (assisted_manual or auto_convert)
        """
        target_db = self._find_job_db_path(job_id) or self.db_path
        conn = self._connect(target_db)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE jobs
            SET mode = ?, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
        """, (mode, job_id))
        
        conn.commit()
        conn.close()
    
    def delete_job(self, job_id: str) -> bool:
        """Delete a job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was deleted, False if not found
        """
        target_db = self._find_job_db_path(job_id) or self.db_path
        conn = self._connect(target_db)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted

