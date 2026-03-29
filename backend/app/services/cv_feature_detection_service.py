"""CV-based Feature Detection Service - Uses modular CV detector."""

from __future__ import annotations  # defer np.ndarray annotation evaluation

import os
import json
try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    cv2 = None  # type: ignore[assignment]
    _CV2_AVAILABLE = False
    try:
        import numpy as np
    except Exception:
        np = None  # type: ignore[assignment]
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

from app.storage.file_storage import FileStorage
# Import the data models and helper functions
from app.services.feature_detection import (
    CVFeatureDetector,
    DetectionResult,
    DetectedFeatures,
    HoleFeature,
    SlotFeature,
    FeatureMerger,
    FeatureMeta,
    create_feature_meta,
)

class CVFeatureDetectionService:
    """Service for detecting holes and slots using modular CV detector."""

    def __init__(self):
        """Initialize CV feature detection service."""
        self.file_storage = FileStorage()
        self.cv_detector = CVFeatureDetector()
        self.feature_merger = FeatureMerger()
        # Initialize the feature flag (used in detect_features_cv)
        self.feature_flag = os.getenv("FEATURE_CV_DETECT", "1") == "1"

    def detect_features_cv(self, job_id: str) -> Dict[str, Any]:
        """Detect geometric features using computer vision."""
        if not self.feature_flag:
            return {
                "success": False,
                "error": "CV feature detection is disabled (FEATURE_CV_DETECT=0)",
                "features": None
            }

        try:
            # 1. Get view information
            view_info = self._get_selected_view_info(job_id)
            if not view_info:
                return {"success": False, "error": "No valid view found for detection", "features": None}

            # 2. Load page image
            page_image = self._load_page_image(job_id, view_info["page"])
            if page_image is None:
                return {"success": False, "error": f"Could not load image for page {view_info['page']}", "features": None}

            # 3. Crop to the selected view
            view_crop = self._crop_to_view(page_image, view_info)

            # 4. Get scale information
            scale_info = self._get_scale_info(job_id)

            # 5. Detect holes and slots
            detected_features = self._detect_features_in_crop(view_crop, scale_info, view_info, job_id)

            # 6. Create result
            result = {
                "success": True,
                "features": detected_features.model_dump() if hasattr(detected_features, 'model_dump') else detected_features.to_dict(),
                "view_info": view_info,
                "scale_info": scale_info,
                "detection_meta": {
                    "model_version": "0.1.0",
                    "detector_version": "cv_v1",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                }
            }

            # 7. Save features to outputs/features_cv.json
            self._save_features(job_id, result)

            return result

        except Exception as e:
            return {
                "success": False,
                "error": f"CV detection failed: {str(e)}",
                "features": None
            }

    # --- Helper Methods ---

    def _load_page_image(self, job_id: str, page_num: int) -> Optional[np.ndarray]:
        """Load the page image for the given page number."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        page_file = outputs_path / "pdf_pages" / f"page_{page_num}.png"

        if not page_file.exists():
            return None

        return cv2.imread(str(page_file))

    def _crop_to_view(self, page_image: np.ndarray, view_info: Dict[str, Any]) -> np.ndarray:
        """Crop the page image based on bounding box pixels."""
        bbox_pixels = view_info.get("bbox_pixels")
        if not bbox_pixels or len(bbox_pixels) != 4:
            return page_image

        x, y, w, h = bbox_pixels
        # Ensure we don't crop outside image boundaries
        y_start, y_end = max(0, int(y)), min(page_image.shape[0], int(y + h))
        x_start, x_end = max(0, int(x)), min(page_image.shape[1], int(x + w))
        
        return page_image[y_start:y_end, x_start:x_end]

    def _get_selected_view_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Load best view info from auto-detect results and enrich with bbox_pixels."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        auto_detect_file = outputs_path / "auto_detect_results.json"
        if not auto_detect_file.exists():
            return None

        try:
            with open(auto_detect_file, "r") as f:
                auto_detect = json.load(f)
        except Exception:
            return None

        best_view = auto_detect.get("best_view")
        ranked_views = auto_detect.get("ranked_views") or []

        view = best_view or (ranked_views[0] if ranked_views else None)
        if not view:
            return None

        page = view.get("page")
        view_index = view.get("view_index")
        bbox = view.get("bbox")
        bbox_pixels = view.get("bbox_pixels")

        if page is None or view_index is None:
            return None

        # Enrich with bbox_pixels from pdf_views if missing
        if not bbox_pixels:
            views_file = outputs_path / "pdf_views" / f"page_{page}_views.json"
            if views_file.exists():
                try:
                    with open(views_file, "r") as f:
                        page_views = json.load(f)
                    views = page_views.get("views", [])
                    if isinstance(view_index, int) and 0 <= view_index < len(views):
                        bbox_pixels = views[view_index].get("bbox_pixels")
                        bbox = bbox or views[view_index].get("bbox")
                except Exception:
                    pass

        if not bbox or not bbox_pixels:
            return None

        return {
            "page": page,
            "view_index": view_index,
            "bbox": bbox,
            "bbox_pixels": bbox_pixels,
        }

    def _get_scale_info(self, job_id: str) -> Dict[str, Any]:
        """Load scale report for pixel-to-inch conversion."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        scale_file = outputs_path / "scale_report.json"
        if scale_file.exists():
            try:
                with open(scale_file, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {"inch_per_pixel": 0.01}

    def _detect_features_in_crop(
        self,
        view_crop: np.ndarray,
        scale_info: Dict[str, Any],
        view_info: Dict[str, Any],
        job_id: str,
    ) -> DetectedFeatures:
        """Run CV detection inside the selected view crop and convert to inches."""
        holes = self.cv_detector._detect_holes_cv(view_crop, view_info)  # type: ignore[attr-defined]
        slots = self.cv_detector._detect_slots_cv(view_crop, view_info)  # type: ignore[attr-defined]

        inch_per_pixel = float(scale_info.get("inch_per_pixel") or 0.01)

        for hole in holes:
            if hole.geometry_px:
                hole.geometry_in = self._convert_geometry_to_inches(hole.geometry_px, inch_per_pixel)
                if isinstance(hole.geometry_in, dict) and hole.geometry_in.get("diameter"):
                    hole.diameter = float(hole.geometry_in["diameter"])

        for slot in slots:
            if slot.geometry_px:
                slot.geometry_in = self._convert_geometry_to_inches(slot.geometry_px, inch_per_pixel)
                if isinstance(slot.geometry_in, dict):
                    if slot.geometry_in.get("width"):
                        slot.width = float(slot.geometry_in["width"])
                    if slot.geometry_in.get("length"):
                        slot.length = float(slot.geometry_in["length"])

        meta = create_feature_meta(model_version="0.1.0", detector_version="cv_v1")

        return DetectedFeatures(
            holes=holes,
            slots=slots,
            chamfers=[],
            fillets=[],
            threads=[],
            meta=meta,
        )

    def _convert_geometry_to_inches(self, geometry_px: Dict[str, Any], inch_per_pixel: float) -> Dict[str, Any]:
        """Convert pixel geometry to inches."""
        geometry_in: Dict[str, Any] = {}
        for key, value in geometry_px.items():
            if isinstance(value, (int, float)):
                geometry_in[key] = float(value) * inch_per_pixel
            elif isinstance(value, list):
                geometry_in[key] = [
                    (float(v) * inch_per_pixel if isinstance(v, (int, float)) else v)
                    for v in value
                ]
            else:
                geometry_in[key] = value
        return geometry_in

    def _save_features(self, job_id: str, result: Dict[str, Any]) -> None:
        """Save CV-detected features to outputs/features_cv.json."""
        outputs_path = self.file_storage.get_outputs_path(job_id)
        features_file = outputs_path / "features_cv.json"
        with open(features_file, "w") as f:
            json.dump(result, f, indent=2)

    def merge_cv_with_text_features(self, job_id: str) -> bool:
        """Merge CV and text features into part_summary.json."""
        try:
            merge_result = self.feature_merger.merge_features(job_id, self.file_storage)
            return bool(merge_result.success)
        except Exception:
            return False