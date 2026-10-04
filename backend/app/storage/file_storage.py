"""File storage utilities."""

import os
import zipfile
import shutil
import io
from pathlib import Path
from typing import List, Tuple
from fastapi import UploadFile, HTTPException

from app.storage.paths import jobs_root, legacy_jobs_roots
from app.storage.document_registry import DocumentRegistry


class FileStorage:
    """Handles file storage operations for jobs."""

    def __init__(self, base_path: str | Path | None = None):
        """Initialize file storage.

        Args:
            base_path: Base path for job storage
        """
        self.base_path = Path(base_path).resolve() if base_path is not None else jobs_root()
        self.read_base_paths = [self.base_path, *legacy_jobs_roots()]
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_job_path(self, job_id: str, base_path: Path | None = None) -> Path:
        """Get the storage path for a job."""
        return (base_path or self.base_path) / job_id

    def _candidate_job_paths(self, job_id: str) -> List[Path]:
        paths: List[Path] = []
        for base_path in self.read_base_paths:
            job_path = self.get_job_path(job_id, base_path)
            if job_path not in paths:
                paths.append(job_path)
        return paths

    def get_inputs_path(self, job_id: str, base_path: Path | None = None) -> Path:
        """Get the inputs directory path for a job."""
        return self.get_job_path(job_id, base_path) / "inputs"

    def get_outputs_path(self, job_id: str, base_path: Path | None = None) -> Path:
        """Get the outputs directory path for a job."""
        return self.get_job_path(job_id, base_path) / "outputs"

    def ensure_job_directories(self, job_id: str) -> None:
        """Ensure job directories exist."""
        self.get_inputs_path(job_id).mkdir(parents=True, exist_ok=True)
        self.get_outputs_path(job_id).mkdir(parents=True, exist_ok=True)

    def save_uploaded_file(self, job_id: str, file: UploadFile) -> str:
        """Save an uploaded file to job inputs.

        Returns:
            Relative path to saved file
        """
        return self._save_stream(job_id, file.filename or "file", file.file)

    def save_bytes_file(self, job_id: str, filename: str, content: bytes) -> str:
        """Save raw bytes as an input file for a job.

        Returns:
            Relative path to saved file
        """
        return self._save_stream(job_id, filename, io.BytesIO(content))

    def _save_stream(self, job_id: str, filename: str, source) -> str:
        self.ensure_job_directories(job_id)
        inputs = self.get_inputs_path(job_id)
        safe_name = self._sanitize_filename(filename)
        stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
        counter = 0
        while True:
            path = inputs / (safe_name if counter == 0 else f"{stem}_{counter}{suffix}")
            try:
                target = path.open("xb")
                break
            except FileExistsError:
                counter += 1
        try:
            with target:
                shutil.copyfileobj(source, target)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        relative_path = f"inputs/{path.name}"
        DocumentRegistry(self.get_job_path(job_id)).register(path, relative_path, filename)
        return relative_path

    def extract_zip(self, job_id: str, zip_path: Path) -> List[str]:
        """Extract supported CAD/doc files from a ZIP archive.

        Returns:
            List of extracted file paths (relative to job directory)
        """
        self.ensure_job_directories(job_id)
        inputs_path = self.get_inputs_path(job_id)
        extracted_files: List[str] = []
        allowed_exts = {".pdf", ".step", ".stp"}

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for member in zip_ref.namelist():
                    member_ext = Path(member).suffix.lower()
                    if member.endswith("/") or member_ext not in allowed_exts:
                        continue

                    safe_filename = self._sanitize_filename(Path(member).name)
                    safe_ext = Path(safe_filename).suffix.lower()
                    if safe_ext not in allowed_exts:
                        continue

                    if safe_filename.startswith("~$"):
                        continue
                    with zip_ref.open(member) as source:
                        extracted_files.append(self._save_stream(job_id, member, source))

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error extracting ZIP: {str(e)}")

        return extracted_files

    # ✅ FIXED: recursive listing for inputs (recommended)
    def list_input_files(self, job_id: str) -> List[str]:
        """List all input files for a job (recursive)."""
        files: List[str] = []
        for job_path in self._candidate_job_paths(job_id):
            inputs_path = job_path / "inputs"
            if not inputs_path.exists():
                continue
            for file_path in inputs_path.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(job_path)
                    rel_str = str(rel).replace("\\", "/")
                    if rel_str not in files:
                        files.append(rel_str)

        return sorted(files)

    # ✅ FIXED: recursive listing for outputs (REQUIRED for pdf_pages, pdf_views)
    def list_output_files(self, job_id: str) -> List[str]:
        """List all output files for a job (recursive)."""
        files: List[str] = []
        for job_path in self._candidate_job_paths(job_id):
            outputs_path = job_path / "outputs"
            if not outputs_path.exists():
                continue
            for file_path in outputs_path.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to(job_path)
                    rel_str = str(rel).replace("\\", "/")
                    if rel_str not in files:
                        files.append(rel_str)

        return sorted(files)

    def get_file_info(self, job_id: str, relative_path: str) -> Tuple[Path, str, int]:
        """Get file information.

        Returns:
            Tuple of (full_path, filename, size)

        Raises:
            HTTPException: If file not found or path is invalid
        """
        if not self._is_safe_path(relative_path):
            raise HTTPException(status_code=400, detail="Invalid file path")

        for job_path in self._candidate_job_paths(job_id):
            file_path = job_path / relative_path

            try:
                file_path.resolve().relative_to(job_path.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail="Path traversal detected")

            if file_path.exists() and file_path.is_file():
                return file_path, file_path.name, file_path.stat().st_size

        raise HTTPException(status_code=404, detail="File not found")

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to prevent path traversal."""
        filename = Path(filename).name

        dangerous_chars = ["..", "/", "\\", "\x00"]
        for char in dangerous_chars:
            filename = filename.replace(char, "_")

        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:250] + ext

        return filename or "file"

    def _is_safe_path(self, path: str) -> bool:
        """Check if path is safe (no path traversal)."""
        if ".." in path:
            return False

        if os.path.isabs(path):
            return False

        dangerous_patterns = ["../", "..\\", "/etc/", "C:\\", "D:\\"]
        for pattern in dangerous_patterns:
            if pattern in path:
                return False

        return True





