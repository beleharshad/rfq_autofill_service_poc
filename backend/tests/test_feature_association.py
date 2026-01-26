"""Unit tests for feature-view association logic."""

import pytest
from unittest.mock import MagicMock
from app.services.feature_detection_service import FeatureDetectionService
from app.services.feature_detection import HoleFeature, DetectedFeatures, create_feature_meta


class TestFeatureAssociation:
    """Test feature to view association logic."""

    @pytest.fixture
    def service(self):
        """Create a feature detection service instance."""
        return FeatureDetectionService()

    @pytest.fixture
    def sample_views(self):
        """Sample view data for testing."""
        return {
            "pages": {
                0: [  # Page 0 views
                    {
                        "bbox": [0.1, 0.1, 0.4, 0.4],  # Center at (0.25, 0.25)
                        "bbox_pixels": [100, 100, 300, 300],
                        "area": 90000,
                        "confidence": 0.8
                    },
                    {
                        "bbox": [0.6, 0.6, 0.9, 0.9],  # Center at (0.75, 0.75)
                        "bbox_pixels": [600, 600, 300, 300],
                        "area": 90000,
                        "confidence": 0.7
                    }
                ],
                1: [  # Page 1 views
                    {
                        "bbox": [0.2, 0.2, 0.5, 0.5],  # Center at (0.35, 0.35)
                        "bbox_pixels": [200, 200, 300, 300],
                        "area": 90000,
                        "confidence": 0.9
                    }
                ]
            },
            "best_view": {
                "page": 0,
                "view_index": 0,
                "bbox": [0.1, 0.1, 0.4, 0.4],
                "bbox_pixels": [100, 100, 300, 300]
            },
            "auto_detect_available": True
        }

    def test_find_nearest_view_exact_match(self, service, sample_views):
        """Test finding nearest view when text bbox overlaps with view."""
        # Text bbox exactly matches first view
        text_bbox = [0.1, 0.1, 0.4, 0.4]
        page_views = sample_views["pages"][0]

        result = service._find_nearest_view(text_bbox, page_views)

        assert result is not None
        view, confidence = result
        assert view["bbox"] == [0.1, 0.1, 0.4, 0.4]
        assert confidence == 1.0  # Exact match = max confidence

    def test_find_nearest_view_close_match(self, service, sample_views):
        """Test finding nearest view when text bbox is close to a view."""
        # Text bbox close to first view center
        text_bbox = [0.2, 0.2, 0.3, 0.3]  # Center at (0.25, 0.25)
        page_views = sample_views["pages"][0]

        result = service._find_nearest_view(text_bbox, page_views)

        assert result is not None
        view, confidence = result
        assert view["bbox"] == [0.1, 0.1, 0.4, 0.4]  # Should match first view
        assert confidence > 0.9  # Very close, high confidence

    def test_find_nearest_view_second_view(self, service, sample_views):
        """Test finding nearest view when text bbox is closer to second view."""
        # Text bbox closer to second view
        text_bbox = [0.7, 0.7, 0.8, 0.8]  # Center at (0.75, 0.75)
        page_views = sample_views["pages"][0]

        result = service._find_nearest_view(text_bbox, page_views)

        assert result is not None
        view, confidence = result
        assert view["bbox"] == [0.6, 0.6, 0.9, 0.9]  # Should match second view
        assert confidence > 0.9  # Very close, high confidence

    def test_find_nearest_view_no_views(self, service):
        """Test finding nearest view when no views are available."""
        text_bbox = [0.2, 0.2, 0.3, 0.3]
        page_views = []

        result = service._find_nearest_view(text_bbox, page_views)

        assert result is None

    def test_associate_single_feature_with_bbox(self, service, sample_views):
        """Test associating a feature with bbox to nearest view."""
        feature = HoleFeature(
            confidence=0.8,
            source_page=0,
            source_bbox=[0.15, 0.15, 0.25, 0.25],  # Close to first view
            notes="Ø0.25 DRILL",
            diameter=0.25,
            kind="cross"
        )

        warnings = []
        service._associate_single_feature(feature, sample_views, warnings)

        assert feature.source_view_index == 0  # Index of matched view
        assert feature.assigned_view_bbox == [0.1, 0.1, 0.4, 0.4]
        assert feature.view_association_confidence > 0.8
        assert len(warnings) == 0

    def test_associate_single_feature_no_bbox_best_view_same_page(self, service, sample_views):
        """Test associating a feature without bbox when best view is on same page."""
        feature = HoleFeature(
            confidence=0.8,
            source_page=0,  # Same page as best view
            notes="Ø0.25 DRILL",
            diameter=0.25,
            kind="cross"
        )

        warnings = []
        service._associate_single_feature(feature, sample_views, warnings)

        assert feature.source_view_index == 0  # Best view index
        assert feature.assigned_view_bbox == [0.1, 0.1, 0.4, 0.4]
        assert feature.view_association_confidence == 0.7  # Medium confidence for fallback
        assert len(warnings) == 0

    def test_associate_single_feature_no_bbox_different_page(self, service, sample_views):
        """Test associating a feature without bbox when best view is on different page."""
        feature = HoleFeature(
            confidence=0.8,
            source_page=1,  # Different page from best view
            notes="Ø0.25 DRILL",
            diameter=0.25,
            kind="cross"
        )

        warnings = []
        service._associate_single_feature(feature, sample_views, warnings)

        # Should assign to largest view on page 1
        assert feature.source_view_index == 0  # Only view on page 1
        assert feature.assigned_view_bbox == [0.2, 0.2, 0.5, 0.5]
        assert feature.view_association_confidence == 0.5  # Medium-low confidence
        assert len(warnings) == 1
        assert "assigned to largest view" in warnings[0]

    def test_associate_single_feature_no_views(self, service):
        """Test associating a feature when no views are available."""
        view_data = {
            "pages": {},
            "best_view": None,
            "auto_detect_available": False
        }

        feature = HoleFeature(
            confidence=0.8,
            source_page=0,
            notes="Ø0.25 DRILL",
            diameter=0.25,
            kind="cross"
        )

        warnings = []
        service._associate_single_feature(feature, view_data, warnings)

        assert feature.source_view_index is None
        assert feature.assigned_view_bbox is None
        assert feature.view_association_confidence == 0.0
        assert len(warnings) == 1
        assert "No views available" in warnings[0]

    def test_associate_features_with_views_integration(self, service, sample_views):
        """Test the full association workflow with multiple features."""
        # Create test features
        features = DetectedFeatures(
            holes=[
                HoleFeature(
                    confidence=0.8,
                    source_page=0,
                    source_bbox=[0.15, 0.15, 0.25, 0.25],  # Close to first view
                    notes="Ø0.25 DRILL",
                    diameter=0.25,
                    kind="cross"
                ),
                HoleFeature(
                    confidence=0.8,
                    source_page=1,  # No bbox, different page
                    notes="Ø0.375 DRILL",
                    diameter=0.375,
                    kind="axial"
                )
            ],
            slots=[],
            chamfers=[],
            fillets=[],
            threads=[],
            meta=create_feature_meta()
        )

        result = service._associate_features_with_views(features, sample_views, "test_job")

        # Check first hole (with bbox)
        hole1 = result.holes[0]
        assert hole1.source_view_index == 0
        assert hole1.assigned_view_bbox == [0.1, 0.1, 0.4, 0.4]
        assert hole1.view_association_confidence > 0.8

        # Check second hole (no bbox, different page)
        hole2 = result.holes[1]
        assert hole2.source_view_index == 0  # Largest view on page 1
        assert hole2.assigned_view_bbox == [0.2, 0.2, 0.5, 0.5]
        assert hole2.view_association_confidence == 0.5

        # Check warnings were added
        assert len(result.meta.warnings) >= 1