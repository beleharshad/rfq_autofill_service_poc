"""Job metadata storage."""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from app.models.job import JobResponse, JobStatus


class JobStorage:
    """Handles job metadata storage in SQLite."""
    
    def __init__(self, db_path: str = "data/jobs.db"):
        """Initialize job storage.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        # Parse datetime - SQLite returns string in format "YYYY-MM-DD HH:MM:SS"
        def parse_datetime(dt_str: str) -> datetime:
            """Parse SQLite datetime string."""
            if isinstance(dt_str, datetime):
                return dt_str
            # Try ISO format first
            try:
                return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            except ValueError:
                # Try SQLite format "YYYY-MM-DD HH:MM:SS"
                try:
                    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    # Fallback to current time if parsing fails
                    return datetime.now()
        
        # Get mode, handling missing column in old records
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
            input_files=[],  # Will be populated by service
            output_files=[],
            created_at=parse_datetime(row['created_at']),
            updated_at=parse_datetime(row['updated_at'])
        )
    
    def update_job_status(self, job_id: str, status: str) -> None:
        """Update job status.
        
        Args:
            job_id: Job identifier
            status: New status
        """
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        
        # Parse datetime - SQLite returns string in format "YYYY-MM-DD HH:MM:SS"
        def parse_datetime(dt_str: str) -> datetime:
            """Parse SQLite datetime string."""
            if isinstance(dt_str, datetime):
                return dt_str
            # Try ISO format first
            try:
                return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            except ValueError:
                # Try SQLite format "YYYY-MM-DD HH:MM:SS"
                try:
                    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    # Fallback to current time if parsing fails
                    return datetime.now()
        
        result = []
        for row in rows:
            # Get mode, handling missing column in old records
            mode = None
            try:
                mode = row['mode']
            except (KeyError, IndexError):
                pass
            
            result.append(JobResponse(
                job_id=row['job_id'],
                name=row['name'],
                description=row['description'],
                status=row['status'],
                mode=mode,
                input_files=[],
                output_files=[],
                created_at=parse_datetime(row['created_at']),
                updated_at=parse_datetime(row['updated_at'])
            ))
        return result
    
    def update_job_mode(self, job_id: str, mode: str) -> None:
        """Update job mode.
        
        Args:
            job_id: Job identifier
            mode: Job mode (assisted_manual or auto_convert)
        """
        conn = sqlite3.connect(self.db_path)
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
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
        deleted = cursor.rowcount > 0
        
        conn.commit()
        conn.close()
        
        return deleted

