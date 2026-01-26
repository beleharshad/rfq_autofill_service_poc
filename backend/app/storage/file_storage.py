"""File storage utilities."""

import os
import zipfile
import shutil
from pathlib import Path
from typing import List, Tuple
from fastapi import UploadFile, HTTPException


class FileStorage:
    """Handles file storage operations for jobs."""

    def __init__(self, base_path: str = "data/jobs"):
        """Initialize file storage.

        Args:
            base_path: Base path for job storage
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def get_job_path(self, job_id: str) -> Path:
        """Get the storage path for a job."""
        return self.base_path / job_id

    def get_inputs_path(self, job_id: str) -> Path:
        """Get the inputs directory path for a job."""
        return self.get_job_path(job_id) / "inputs"

    def get_outputs_path(self, job_id: str) -> Path:
        """Get the outputs directory path for a job."""
        return self.get_job_path(job_id) / "outputs"

    def ensure_job_directories(self, job_id: str) -> None:
        """Ensure job directories exist."""
        self.get_inputs_path(job_id).mkdir(parents=True, exist_ok=True)
        self.get_outputs_path(job_id).mkdir(parents=True, exist_ok=True)

    def save_uploaded_file(self, job_id: str, file: UploadFile) -> str:
        """Save an uploaded file to job inputs.

        Returns:
            Relative path to saved file
        """
        self.ensure_job_directories(job_id)
        inputs_path = self.get_inputs_path(job_id)

        safe_filename = self._sanitize_filename(file.filename)
        file_path = inputs_path / safe_filename

        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        return f"inputs/{safe_filename}"

    def extract_zip(self, job_id: str, zip_path: Path) -> List[str]:
        """Extract PDF files from a ZIP archive.

        Returns:
            List of extracted PDF file paths (relative to job directory)
        """
        self.ensure_job_directories(job_id)
        inputs_path = self.get_inputs_path(job_id)
        extracted_files: List[str] = []

        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                for member in zip_ref.namelist():
                    if member.endswith("/") or not member.lower().endswith(".pdf"):
                        continue

                    safe_filename = self._sanitize_filename(Path(member).name)
                    if not safe_filename.lower().endswith(".pdf"):
                        continue

                    extracted_path = inputs_path / safe_filename

                    counter = 1
                    base_name = extracted_path.stem
                    while extracted_path.exists():
                        extracted_path = inputs_path / f"{base_name}_{counter}.pdf"
                        counter += 1

                    with zip_ref.open(member) as source, open(extracted_path, "wb") as target:
                        shutil.copyfileobj(source, target)

                    extracted_files.append(f"inputs/{extracted_path.name}")

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid ZIP file")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error extracting ZIP: {str(e)}")

        return extracted_files

    # ✅ FIXED: recursive listing for inputs (recommended)
    def list_input_files(self, job_id: str) -> List[str]:
        """List all input files for a job (recursive)."""
        inputs_path = self.get_inputs_path(job_id)
        if not inputs_path.exists():
            return []

        files: List[str] = []
        job_path = self.get_job_path(job_id)

        for file_path in inputs_path.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(job_path)
                files.append(str(rel).replace("\\", "/"))

        return sorted(files)

    # ✅ FIXED: recursive listing for outputs (REQUIRED for pdf_pages, pdf_views)
    def list_output_files(self, job_id: str) -> List[str]:
        """List all output files for a job (recursive)."""
        outputs_path = self.get_outputs_path(job_id)
        if not outputs_path.exists():
            return []

        files: List[str] = []
        job_path = self.get_job_path(job_id)

        for file_path in outputs_path.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(job_path)
                files.append(str(rel).replace("\\", "/"))

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

        job_path = self.get_job_path(job_id)
        file_path = job_path / relative_path

        try:
            file_path.resolve().relative_to(job_path.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Path traversal detected")

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        return file_path, file_path.name, file_path.stat().st_size

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






