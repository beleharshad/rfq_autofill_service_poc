"""Feature Detection Service - Orchestrates feature detection using the modular system."""

import json
import math
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    _CV2_AVAILABLE = False
import numpy as np
import pdfplumber
from app.storage.file_storage import FileStorage

from app.services.feature_detection import (
    TextFeatureDetector, 
    FeatureMerger, 
    DetectedFeatures, 
    AnyFeature
)

class FeatureDetectionService:
    """
    Service for detecting geometric features using modular detectors.
    
    This service acts as an orchestrator that:
    1. Validates input files.
    2. Delegates actual detection to TextFeatureDetector.
    3. Loads geometric view data (from Computer Vision steps).
    4. Associates text features with specific views (Front, Top, etc.).
    5. Saves results and orchestrates the merging into the part summary.
    """

    def __init__(self):
        """Initialize feature detection service and dependencies."""
        self.file_storage = FileStorage()
        self.text_detector = TextFeatureDetector()
        self.feature_merger = FeatureMerger()

    def detect_features_text(self, job_id: str) -> Dict[str, Any]:
        """
        Detect geometric features from PDF text using the modular text detector.

        Args:
            job_id: Job identifier

        Returns:
            Dictionary with detected features and metadata
        """
        try:
            # 1. Get PDF path
            inputs_path = self.file_storage.get_inputs_path(job_id)
            pdf_path = inputs_path / "source.pdf"

            if not pdf_path.exists():
                return {
                    "success": False,
                    "error": "Source PDF not found",
                    "features": None
                }

            # 2. If the PDF has no extractable text, run OCR and use that output
            ocr_used = False
            pdf_path_to_use = pdf_path
            if not self._pdf_has_text(pdf_path):
                ocr_pdf = self._ensure_ocr_pdf(job_id, pdf_path)
                pdf_path_to_use = ocr_pdf
                ocr_used = True

            # 3. Run text detection (Delegated to modular detector)
            detection_result = self.text_detector.detect_features(pdf_path_to_use)

            if not detection_result.success:
                return {
                    "success": False,
                    "error": detection_result.error,
                    "features": None
                }

            # 4. Associate features with views (Service Logic)
            # We glue the text results to the CV view results here
            if detection_result.features:
                view_data = self._load_view_data(job_id)
                detection_result.features = self._associate_features_with_views(
                    detection_result.features, view_data, job_id
                )
                if ocr_used:
                    warnings = detection_result.features.meta.warnings or []
                    if "OCR_USED" not in warnings:
                        warnings.append("OCR_USED")
                    detection_result.features.meta.warnings = warnings

            # 5. Prepare Result
            timestamp = datetime.now(timezone.utc).isoformat()
            if detection_result.features and detection_result.features.meta.timestamp_utc:
                timestamp = detection_result.features.meta.timestamp_utc

            result = {
                "success": True,
                "features": detection_result.features.model_dump() if detection_result.features else None,
                "page_count": detection_result.page_count,
                "total_candidates": detection_result.total_candidates,
                "detection_meta": {
                    "model_version": detection_result.features.meta.model_version if detection_result.features else "unknown",
                    "detector_version": detection_result.features.meta.detector_version if detection_result.features else "unknown",
                    "timestamp_utc": timestamp,
                }
            }

            # 6. Save features to outputs/features_text.json
            self._save_features(job_id, result)

            return result

        except Exception as e:
            return {
                "success": False,
                "error": f"Text feature detection failed: {str(e)}",
                "features": None
            }

    def merge_features_into_part_summary(self, job_id: str) -> bool:
        """
        Merge detected features into part_summary.json using the modular merger.

        Args:
            job_id: Job identifier

        Returns:
            True if merge was successful, False otherwise
        """
        try:
            # Delegate strictly to the FeatureMerger class
            merge_result = self.feature_merger.merge_features(job_id, self.file_storage)
            return merge_result.success
        except Exception as e:
            print(f"Error orchestrating feature merge: {e}")
            return False

    def _pdf_has_text(self, pdf_path: Path) -> bool:
        """Return True if the PDF contains extractable text."""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text() or ""
                    if text.strip():
                        return True
        except Exception:
            return False
        return False

    def _ensure_ocr_pdf(self, job_id: str, pdf_path: Path) -> Path:
        """Create a searchable PDF using OCRmyPDF when text is missing."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        ocr_pdf = outputs_path / "source_ocr.pdf"
        if ocr_pdf.exists():
            return ocr_pdf

        try:
            env = os.environ.copy()
            tesseract_roots = [
                r"C:\Program Files\Tesseract-OCR",
                r"C:\Program Files (x86)\Tesseract-OCR",
            ]
            tesseract_root = next((p for p in tesseract_roots if os.path.exists(p)), None)
            if tesseract_root:
                if not env.get("TESSDATA_PREFIX"):
                    env["TESSDATA_PREFIX"] = os.path.join(tesseract_root, "tessdata")
                if tesseract_root not in env.get("PATH", ""):
                    env["PATH"] = f"{tesseract_root};" + env.get("PATH", "")

            result = subprocess.run(
                [
                    "ocrmypdf",
                    "--force-ocr",
                    str(pdf_path),
                    str(ocr_pdf),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
        except FileNotFoundError as e:
            raise RuntimeError("ocrmypdf is not installed or not on PATH") from e

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"OCR failed: {stderr or 'unknown error'}")

        return ocr_pdf

    def validate_feature_quality(self, job_id: str) -> Dict[str, Any]:
        """
        Validate feature detection quality and return quality assessment.
        Checks if files exist and if the detection flagged warnings.
        """
        quality_issues = []
        status = "PASSED"

        try:
            outputs_path = self.file_storage.get_outputs_path(job_id)
            text_file = outputs_path / "features_text.json"
            cv_file = outputs_path / "features_cv.json"
            summary_file = outputs_path / "part_summary.json"

            scale_method = None
            if summary_file.exists():
                try:
                    with open(summary_file, "r") as f:
                        summary_data = json.load(f)
                    scale_method = (summary_data.get("scale_report") or {}).get("method")
                except Exception:
                    scale_method = None

            if not text_file.exists():
                return {
                    "status": "FAILED",
                    "issues": ["Feature text file not found"]
                }

            with open(text_file, 'r') as f:
                text_data = json.load(f)

            if not text_data.get("success"):
                status = "FAILED"
                quality_issues.append(f"Detection error: {text_data.get('error')}")
            
            features = text_data.get("features", {})
            if features:
                meta = features.get("meta", {})
                warnings = meta.get("warnings", [])
                if warnings:
                    status = "WARNING"
                    quality_issues.extend(warnings)
                
                # Check for empty results which might indicate parse failure
                total_features = (
                    len(features.get("holes", [])) + 
                    len(features.get("slots", [])) + 
                    len(features.get("threads", []))
                )
                if total_features == 0:
                    status = "WARNING"
                    quality_issues.append("No features detected (possible parsing issue or empty part)")

                # SLOT_DIM_MISSING: missing width/length on any slot
                for slot in features.get("slots", []):
                    if not isinstance(slot, dict):
                        continue
                    if not slot.get("width") or not slot.get("length"):
                        quality_issues.append("SLOT_DIM_MISSING")
                        status = "NEEDS_REVIEW"
                        break

                # THREAD_UNRESOLVED: missing designation on any thread
                for thread in features.get("threads", []):
                    if not isinstance(thread, dict):
                        continue
                    designation = thread.get("designation")
                    if not designation or not str(designation).strip():
                        quality_issues.append("THREAD_UNRESOLVED")
                        status = "NEEDS_REVIEW"
                        break

                # HOLE_PATTERN_AMBIGUOUS: text count vs CV count mismatch
                text_count = 0
                for hole in features.get("holes", []):
                    if not isinstance(hole, dict):
                        continue
                    cnt = hole.get("count")
                    if isinstance(cnt, int) and cnt > 1:
                        text_count += cnt

                cv_count = 0
                if cv_file.exists():
                    try:
                        with open(cv_file, "r") as f:
                            cv_data = json.load(f)
                        if cv_data.get("success") and cv_data.get("features"):
                            cv_count = len(cv_data["features"].get("holes", []))
                    except Exception:
                        cv_count = 0

                if text_count > 0 and cv_count > 0 and abs(text_count - cv_count) > 2:
                    quality_issues.append("HOLE_PATTERN_AMBIGUOUS")
                    status = "NEEDS_REVIEW"

                # FEATURES_CV_LOW_CONF: low average confidence in CV results
                if cv_file.exists():
                    try:
                        with open(cv_file, "r") as f:
                            cv_data = json.load(f)
                        if cv_data.get("success") and cv_data.get("features"):
                            cv_features = cv_data["features"]
                            confs = []
                            for h in cv_features.get("holes", []):
                                if isinstance(h, dict) and isinstance(h.get("confidence"), (int, float)):
                                    confs.append(float(h["confidence"]))
                            for s in cv_features.get("slots", []):
                                if isinstance(s, dict) and isinstance(s.get("confidence"), (int, float)):
                                    confs.append(float(s["confidence"]))
                            if confs:
                                avg_conf = sum(confs) / len(confs)
                                if avg_conf < 0.6:
                                    quality_issues.append("FEATURES_CV_LOW_CONF")
                                    status = "NEEDS_REVIEW"
                    except Exception:
                        pass

                # FEATURES_TEXT_ONLY: poorly-scaled geometry + no CV features
                if scale_method not in ("anchor_dimension", "calibrated_from_ocr", "dpi_based"):
                    has_cv = False
                    if cv_file.exists():
                        try:
                            with open(cv_file, "r") as f:
                                cv_data = json.load(f)
                            if cv_data.get("success") and cv_data.get("features"):
                                cv_features = cv_data["features"]
                                has_cv = bool(cv_features.get("holes") or cv_features.get("slots"))
                        except Exception:
                            has_cv = False
                    if not has_cv:
                        quality_issues.append("FEATURES_TEXT_ONLY")
                        status = "NEEDS_REVIEW"

            return {
                "status": status,
                "issues": quality_issues,
                "checked_at": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            return {
                "status": "ERROR",
                "issues": [f"Validation exception: {str(e)}"]
            }

    # =========================================================================
    # Helper Methods: Data Loading & View Association
    # =========================================================================

    def _load_view_data(self, job_id: str) -> Dict[str, Any]:
        """
        Load view detection data (generated by CV service) for feature association.
        Combines per-page view files and auto-detect summary.
        """
        outputs_path = self.file_storage.get_outputs_path(job_id)

        view_data = {
            "pages": {},  # page_num -> list of views
            "best_view": None,  # best view from auto_detect
            "auto_detect_available": False
        }

        # 1. Load views from individual pdf_views directory
        views_dir = outputs_path / "pdf_views"
        if views_dir.exists():
            for views_file in views_dir.glob("page_*_views.json"):
                try:
                    # Filename format: page_1_views.json
                    parts = views_file.stem.split("_")
                    if len(parts) >= 2 and parts[1].isdigit():
                        page_num = int(parts[1])
                        with open(views_file, 'r') as f:
                            page_data = json.load(f)
                            view_data["pages"][page_num] = page_data.get("views", [])
                except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
                    print(f"Warning: Failed to load views for {views_file.name}: {e}")

        # 2. Load best view from auto_detect results
        auto_detect_file = outputs_path / "auto_detect_results.json"
        if auto_detect_file.exists():
            try:
                with open(auto_detect_file, 'r') as f:
                    auto_detect_data = json.load(f)
                    view_data["best_view"] = auto_detect_data.get("best_view")
                    view_data["auto_detect_available"] = True
            except (FileNotFoundError, json.JSONDecodeError) as e:
                print(f"Warning: Failed to load auto_detect results: {e}")

        return view_data

    def _associate_features_with_views(self, detected_features: DetectedFeatures, view_data: Dict[str, Any], job_id: str) -> DetectedFeatures:
        """
        Iterate through all detected features and link them to the geometric views 
        detected by the computer vision system.
        """
        # Process each feature type
        feature_lists = [
            detected_features.holes,
            detected_features.slots,
            detected_features.chamfers,
            detected_features.fillets,
            detected_features.threads
        ]

        warnings = []

        for feature_list in feature_lists:
            for feature in feature_list:
                self._associate_single_feature(feature, view_data, warnings)

        # Update meta with any association warnings
        detected_features.meta.warnings.extend(warnings)

        return detected_features

    def _associate_single_feature(self, feature: AnyFeature, view_data: Dict[str, Any], warnings: List[str]) -> None:
        """
        Associate a single feature with the nearest/best view.
        Prioritizes spatial proximity (bbox) first, then fallback heuristics.
        """
        page_num = feature.source_page
        page_views = view_data["pages"].get(page_num, [])
        best_view_global = view_data.get("best_view")

        # Case 1: No views found on the specific page
        if not page_views:
            feature.view_association_confidence = 0.0
            
            # Fallback: If the "best global view" is on this page, assign it
            if best_view_global and best_view_global.get("page") == page_num:
                feature.source_view_index = best_view_global.get("view_index")
                feature.assigned_view_bbox = best_view_global.get("bbox")
                feature.view_association_confidence = 0.3  # Low confidence fallback
                warnings.append(f"Feature on page {page_num} assigned to global best view (no local views detected)")
            else:
                warnings.append(f"No views available for feature association on page {page_num}")
            return

        # Case 2: We have views on the page. Try to match by Text Bounding Box.
        if feature.source_bbox is not None and len(feature.source_bbox) == 4:
            best_match = self._find_nearest_view(feature.source_bbox, page_views)
            
            if best_match:
                view, distance_score = best_match
                feature.source_view_index = view.get("index", view.get("view_index", 0))
                feature.assigned_view_bbox = view.get("bbox")
                feature.view_association_confidence = distance_score
                return

        # Case 3: No Text Bounding Box (or match failed). Use Heuristics.
        
        # 3a. If global best view is on this page, default to that.
        if best_view_global and best_view_global.get("page") == page_num:
            feature.source_view_index = best_view_global.get("view_index")
            feature.assigned_view_bbox = best_view_global.get("bbox")
            feature.view_association_confidence = 0.7 
            return

        # 3b. Fallback to largest view on the page.
        try:
            largest_view = max(page_views, key=lambda v: self._calculate_area(v.get("bbox", [0,0,0,0])))
            # We need an index. If not in dict, infer from list position
            view_idx = largest_view.get("view_index", page_views.index(largest_view))
            
            feature.source_view_index = view_idx
            feature.assigned_view_bbox = largest_view.get("bbox")
            feature.view_association_confidence = 0.5
            warnings.append(f"Feature on page {page_num} assigned to largest view (heuristic)")
        except (ValueError, IndexError):
            feature.view_association_confidence = 0.0

    def _find_nearest_view(self, text_bbox: List[float], page_views: List[Dict[str, Any]]) -> Optional[Tuple[Dict[str, Any], float]]:
        """
        Find the nearest view to a text bounding box using normalized coordinates.
        Returns: (best_view_dict, confidence_score)
        """
        if not page_views:
            return None

        best_view = None
        best_score = 0.0

        # Calculate text center
        text_cx = (text_bbox[0] + text_bbox[2]) / 2
        text_cy = (text_bbox[1] + text_bbox[3]) / 2

        for i, view in enumerate(page_views):
            view_bbox = view.get("bbox", [])
            if len(view_bbox) != 4:
                continue

            # Calculate view center
            view_cx = (view_bbox[0] + view_bbox[2]) / 2
            view_cy = (view_bbox[1] + view_bbox[3]) / 2

            # Euclidean distance
            distance = math.sqrt((text_cx - view_cx) ** 2 + (text_cy - view_cy) ** 2)

            # Convert distance to confidence score (closer = higher confidence)
            # Max possible distance in normalized 0-1 square is approx 1.414
            confidence = max(0.1, 1.0 - (distance / 1.414))

            if confidence > best_score:
                best_score = confidence
                best_view = view.copy()
                # Ensure index is present
                if "view_index" not in best_view:
                    best_view["view_index"] = i
                if "index" not in best_view:
                    best_view["index"] = best_view["view_index"]

        return (best_view, best_score) if best_view else None

    def _calculate_area(self, bbox: List[float]) -> float:
        """Helper to calculate area of a bounding box [x1, y1, x2, y2]."""
        if len(bbox) != 4:
            return 0.0
        return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])

    def _save_features(self, job_id: str, result: Dict[str, Any]) -> None:
        """Save detected features to storage."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        features_file = outputs_path / "features_text.json"
        with open(features_file, 'w') as f:
            json.dump(result, f, indent=2)