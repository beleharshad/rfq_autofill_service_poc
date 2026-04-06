"""Job service for business logic."""

import uuid
import shutil
from typing import List, Optional
from fastapi import UploadFile, HTTPException
from app.models.job import JobResponse, JobStatus, JobMode
from app.storage.job_storage import JobStorage
from app.storage.file_storage import FileStorage
from app.services.step_analysis_service import StepAnalysisService


class JobService:
    """Service for job operations."""
    
    def __init__(self):
        """Initialize job service."""
        self.job_storage = JobStorage()
        self.file_storage = FileStorage()
        self.step_analysis_service = StepAnalysisService()
    
    def create_job(
        self, 
        name: Optional[str] = None, 
        description: Optional[str] = None,
        mode: Optional[str] = None
    ) -> str:
        """Create a new job.
        
        Args:
            name: Optional job name
            description: Optional job description
            mode: Optional job mode (assisted_manual or auto_convert)
            
        Returns:
            Job ID
        """
        job_id = str(uuid.uuid4())
        self.job_storage.create_job(job_id, name, description, mode)
        self.file_storage.ensure_job_directories(job_id)
        return job_id
    
    def set_job_mode(self, job_id: str, mode: str) -> None:
        """Set job processing mode.
        
        Args:
            job_id: Job identifier
            mode: Job mode (assisted_manual or auto_convert)
            
        Raises:
            HTTPException: If job not found
        """
        job = self.job_storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Validate mode
        if mode not in [JobMode.ASSISTED_MANUAL, JobMode.AUTO_CONVERT]:
            raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")
        
        self.job_storage.update_job_mode(job_id, mode)
    
    def upload_files(self, job_id: str, files: List[UploadFile]) -> JobResponse:
        """Upload files to a job.
        
        Args:
            job_id: Job identifier
            files: List of uploaded files
            
        Returns:
            Updated job response
            
        Raises:
            HTTPException: If job not found or upload fails
        """
        job = self.job_storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        uploaded_files = []
        
        for file in files:
            # Check if file is ZIP
            if file.filename and file.filename.lower().endswith('.zip'):
                # Save ZIP temporarily
                temp_zip_path = self.file_storage.get_inputs_path(job_id) / f"temp_{uuid.uuid4()}.zip"
                with open(temp_zip_path, "wb") as f:
                    shutil.copyfileobj(file.file, f)
                
                # Extract supported files from ZIP
                extracted = self.file_storage.extract_zip(job_id, temp_zip_path)
                uploaded_files.extend(extracted)

                for rel_path in extracted:
                    if rel_path.lower().endswith(('.step', '.stp')):
                        self.step_analysis_service.process_uploaded_step(
                            job_id,
                            self.file_storage.get_job_path(job_id) / rel_path,
                        )
                
                # Remove temporary ZIP
                temp_zip_path.unlink()
            else:
                # Save regular file (PDF / STEP / STP accepted)
                if file.filename and not file.filename.lower().endswith(('.pdf', '.step', '.stp')):
                    continue  # Skip unsupported files
                
                saved_path = self.file_storage.save_uploaded_file(job_id, file)
                uploaded_files.append(saved_path)

                if saved_path.lower().endswith(('.step', '.stp')):
                    self.step_analysis_service.process_uploaded_step(
                        job_id,
                        self.file_storage.get_job_path(job_id) / saved_path,
                    )
        
        # Update job status
        if uploaded_files:
            self.job_storage.update_job_status(job_id, JobStatus.CREATED)
        
        return self.get_job(job_id)
    
    def get_job(self, job_id: str) -> JobResponse:
        """Get job with file lists.
        
        Args:
            job_id: Job identifier
            
        Returns:
            Job response with file lists
            
        Raises:
            HTTPException: If job not found
        """
        job = self.job_storage.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Populate file lists
        job.input_files = self.file_storage.list_input_files(job_id)
        job.output_files = self.file_storage.list_output_files(job_id)
        
        return job
    
    def list_jobs(self) -> List[JobResponse]:
        """List all jobs.
        
        Returns:
            List of job responses
        """
        jobs = self.job_storage.list_jobs()
        # Populate file lists for each job
        for job in jobs:
            job.input_files = self.file_storage.list_input_files(job.job_id)
            job.output_files = self.file_storage.list_output_files(job.job_id)
        return jobs
    
    def delete_job(self, job_id: str) -> None:
        """Delete a job and all its files.
        
        Args:
            job_id: Job identifier
            
        Raises:
            HTTPException: If job not found
        """
        if not self.job_storage.delete_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Delete job directory
        job_path = self.file_storage.get_job_path(job_id)
        if job_path.exists():
            shutil.rmtree(job_path)

