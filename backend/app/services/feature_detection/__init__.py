"""Feature detection module for geometric feature extraction."""

from .schema import (
    FeatureBase, HoleFeature, SlotFeature, ChamferFeature, FilletFeature, ThreadFeature,
    AnyFeature, FeatureMeta, DetectedFeatures, DetectionResult, MergeResult,
    create_feature_meta
)
from .text_detector import TextFeatureDetector
from .cv_detector import CVFeatureDetector
from .merge import FeatureMerger, merge_features_into_part_summary

__all__ = [
    # Schema
    "FeatureBase", "HoleFeature", "SlotFeature", "ChamferFeature", "FilletFeature", "ThreadFeature",
    "AnyFeature", "FeatureMeta", "DetectedFeatures", "DetectionResult", "MergeResult",
    "create_feature_meta",
    # Detectors
    "TextFeatureDetector", "CVFeatureDetector",
    # Merging
    "FeatureMerger", "merge_features_into_part_summary"
]