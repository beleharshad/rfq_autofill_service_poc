"""Feature merging logic for combining text and CV detections."""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone

from .schema import DetectedFeatures, AnyFeature, MergeResult, create_feature_meta


class FeatureMerger:
    """Merger for combining text and CV detected features."""

    def merge_features(self, job_id: str, file_storage) -> MergeResult:
        """
        Merge text and CV features into part_summary.json.

        Args:
            job_id: Job identifier
            file_storage: File storage instance

        Returns:
            MergeResult indicating success and details
        """
        try:
            outputs_path = file_storage.get_outputs_path(job_id)

            # Load existing part_summary.json
            summary_file = outputs_path / "part_summary.json"
            if not summary_file.exists():
                return MergeResult(
                    success=False,
                    error="part_summary.json not found"
                )

            with open(summary_file, 'r') as f:
                part_summary_data = json.load(f)

            # Load text features
            text_features = self._load_text_features(outputs_path)

            # Load CV features
            cv_features = self._load_cv_features(outputs_path)

            # Merge features
            merged_features = self._merge_feature_sets(text_features, cv_features, part_summary_data)

            # Update part summary
            if merged_features:
                part_summary_data["features"] = merged_features.model_dump()

                # Update timestamp
                part_summary_data["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

                # Save updated part_summary.json
                with open(summary_file, 'w') as f:
                    json.dump(part_summary_data, f, indent=2)

                feature_count = (
                    len(merged_features.holes) +
                    len(merged_features.slots) +
                    len(merged_features.chamfers) +
                    len(merged_features.fillets) +
                    len(merged_features.threads)
                )

                return MergeResult(
                    success=True,
                    features_added=feature_count
                )
            else:
                return MergeResult(
                    success=True,
                    features_added=0
                )

        except Exception as e:
            return MergeResult(
                success=False,
                error=f"Merge failed: {str(e)}"
            )

    def _load_text_features(self, outputs_path: Path) -> Optional[DetectedFeatures]:
        """Load text-detected features."""
        try:
            text_file = outputs_path / "features_text.json"
            if not text_file.exists():
                return None

            with open(text_file, 'r') as f:
                text_data = json.load(f)

            if text_data.get("success") and text_data.get("features"):
                return DetectedFeatures.model_validate(text_data["features"])

        except Exception as e:
            print(f"Warning: Failed to load text features: {e}")

        return None

    def _load_cv_features(self, outputs_path: Path) -> Optional[DetectedFeatures]:
        """Load CV-detected features."""
        try:
            cv_file = outputs_path / "features_cv.json"
            if not cv_file.exists():
                return None

            with open(cv_file, 'r') as f:
                cv_data = json.load(f)

            if cv_data.get("success") and cv_data.get("features"):
                return DetectedFeatures.model_validate(cv_data["features"])

        except Exception as e:
            print(f"Warning: Failed to load CV features: {e}")

        return None

    def _merge_feature_sets(
        self,
        text_features: Optional[DetectedFeatures],
        cv_features: Optional[DetectedFeatures],
        part_summary_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[DetectedFeatures]:
        """
        Merge text and CV feature sets.

        Args:
            text_features: Features from text detection
            cv_features: Features from CV detection

        Returns:
            Merged DetectedFeatures or None if no features
        """
        if not text_features and not cv_features:
            return None

        merged = DetectedFeatures(
            holes=[],
            slots=[],
            chamfers=[],
            fillets=[],
            threads=[],
            meta=create_feature_meta("merged-v1.0.0")
        )

        # Merge holes
        if text_features and text_features.holes:
            merged.holes.extend(text_features.holes)
        if cv_features and cv_features.holes:
            merged.holes.extend(cv_features.holes)

        # Merge slots
        if text_features and text_features.slots:
            merged.slots.extend(text_features.slots)
        if cv_features and cv_features.slots:
            merged.slots.extend(cv_features.slots)

        # Text-only features
        if text_features:
            merged.chamfers.extend(text_features.chamfers)
            merged.fillets.extend(text_features.fillets)
            merged.threads.extend(text_features.threads)

        # Cross-validate and boost confidence where text and CV agree
        self._cross_validate_features(merged)

        # Update metadata
        scale_method = None
        if isinstance(part_summary_data, dict):
            scale_method = (part_summary_data.get("scale_report") or {}).get("method")

        merged.meta.warnings = self._collect_warnings(text_features, cv_features, scale_method)

        return merged

    def _cross_validate_features(self, merged_features: DetectedFeatures):
        """
        Cross-validate features between text and CV detections to boost confidence.

        Args:
            merged_features: Features to cross-validate in-place
        """
        # Cross-validate holes
        for hole in merged_features.holes:
            if hasattr(hole, 'geometry_in') and hole.geometry_in:
                # This is likely a CV-detected hole with geometry
                # Check if there's a matching text hole by diameter
                matching_text_holes = [
                    h for h in merged_features.holes
                    if not hasattr(h, 'geometry_in') and abs(h.diameter - hole.diameter) < 0.05
                ]

                if matching_text_holes:
                    # Boost confidence when text and CV agree
                    hole.confidence = min(hole.confidence + 0.2, 1.0)
                    hole.notes = (hole.notes or "") + " [CONFIRMED: matches text detection]"

        # Cross-validate slots
        for slot in merged_features.slots:
            if hasattr(slot, 'geometry_in') and slot.geometry_in:
                # CV-detected slot
                # Check for matching text slots by dimensions (rough match)
                for text_slot in merged_features.slots:
                    if not hasattr(text_slot, 'geometry_in'):
                        # Rough dimension matching
                        width_match = abs(text_slot.width - slot.width) < 0.1
                        length_match = abs(text_slot.length - slot.length) < 0.1

                        if width_match and length_match:
                            slot.confidence = min(slot.confidence + 0.2, 1.0)
                            slot.notes = (slot.notes or "") + " [CONFIRMED: matches text detection]"
                            break

    def _collect_warnings(
        self,
        text_features: Optional[DetectedFeatures],
        cv_features: Optional[DetectedFeatures],
        scale_method: Optional[str] = None,
    ) -> List[str]:
        """Collect warnings from both detection methods."""
        warnings = []

        if text_features and text_features.meta:
            warnings.extend(text_features.meta.warnings)

        if cv_features and cv_features.meta:
            warnings.extend(cv_features.meta.warnings)

        # Add merge-specific warnings
        if text_features and cv_features:
            # Check for potential conflicts
            text_holes = len(text_features.holes) if text_features.holes else 0
            cv_holes = len(cv_features.holes) if cv_features.holes else 0

            if text_holes > 0 and cv_holes > 0:
                warnings.append("Features merged from both text and CV detection")

            # Check for significant count mismatches
            if text_holes > 0 and cv_holes > 0 and abs(text_holes - cv_holes) > 3:
                warnings.append("Significant mismatch between text and CV hole counts")

        # HOLE_PATTERN_AMBIGUOUS: pattern count mismatch with CV
        if text_features and cv_features:
            text_pattern_count = 0
            for hole in text_features.holes:
                if getattr(hole, "count", None) and hole.count and hole.count > 1:
                    text_pattern_count += hole.count
            cv_hole_count = len(cv_features.holes) if cv_features.holes else 0
            if text_pattern_count > 0 and cv_hole_count > 0 and abs(text_pattern_count - cv_hole_count) > 2:
                warnings.append("HOLE_PATTERN_AMBIGUOUS")

        # SLOT_DIM_MISSING: missing width/length
        if text_features:
            for slot in text_features.slots:
                if not getattr(slot, "width", None) or not getattr(slot, "length", None):
                    warnings.append("SLOT_DIM_MISSING")
                    break

        # THREAD_UNRESOLVED: missing designation
        if text_features:
            for thread in text_features.threads:
                designation = getattr(thread, "designation", None)
                if not designation or not str(designation).strip():
                    warnings.append("THREAD_UNRESOLVED")
                    break

        # FEATURES_CV_LOW_CONF: low average confidence in CV detections
        if cv_features:
            confs = []
            for h in cv_features.holes:
                if getattr(h, "confidence", None) is not None:
                    confs.append(float(h.confidence))
            for s in cv_features.slots:
                if getattr(s, "confidence", None) is not None:
                    confs.append(float(s.confidence))
            if confs:
                avg_conf = sum(confs) / len(confs)
                if avg_conf < 0.6:
                    warnings.append("FEATURES_CV_LOW_CONF")

        # FEATURES_TEXT_ONLY: estimated scale + no CV features
        if scale_method == "estimated" and text_features and (not cv_features or (not cv_features.holes and not cv_features.slots)):
            warnings.append("FEATURES_TEXT_ONLY")

        return list(set(warnings))  # Remove duplicates


def merge_features_into_part_summary(job_id: str, file_storage) -> MergeResult:
    """
    Convenience function to merge features into part summary.

    Args:
        job_id: Job identifier
        file_storage: File storage instance

    Returns:
        MergeResult
    """
    merger = FeatureMerger()
    return merger.merge_features(job_id, file_storage)