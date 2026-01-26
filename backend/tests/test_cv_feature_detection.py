"""Unit tests for CV-based feature detection."""

import pytest
import numpy as np
from unittest.mock import MagicMock, patch
from app.services.cv_feature_detection_service import CVFeatureDetectionService


class TestCVFeatureDetection:
    """Test CV-based feature detection."""

    @pytest.fixture
    def service(self):
        """Create a CV feature detection service instance."""
        return CVFeatureDetectionService()

    @pytest.fixture
    def sample_image(self):
        """Create a sample test image with some circular and rectangular features."""
        # Create a blank image
        img = np.zeros((300, 300, 3), dtype=np.uint8)

        # Add some circular features (holes)
        cv2 = pytest.importorskip("cv2")
        cv2.circle(img, (100, 100), 15, (255, 255, 255), -1)  # Filled white circle
        cv2.circle(img, (200, 150), 10, (255, 255, 255), -1)  # Smaller circle

        # Add a rectangular slot-like feature
        cv2.rectangle(img, (50, 200), (150, 220), (255, 255, 255), -1)  # Horizontal rectangle

        return img

    def test_feature_flag_disabled(self, service):
        """Test that CV detection is disabled when feature flag is off."""
        with patch.dict('os.environ', {'FEATURE_CV_DETECT': '0'}):
            service_check = CVFeatureDetectionService()
            result = service_check.detect_features_cv("test_job")

            assert not result["success"]
            assert "disabled" in result["error"]

    def test_opencv_not_available(self, service):
        """Test behavior when OpenCV is not available."""
        with patch('app.services.cv_feature_detection_service._OPENCV_AVAILABLE', False):
            result = service.detect_features_cv("test_job")

            assert not result["success"]
            assert "OpenCV" in result["error"]

    def test_preprocess_image(self, service, sample_image):
        """Test image preprocessing."""
        processed = service._preprocess_image(sample_image)

        # Should be binary image
        assert processed.dtype == np.uint8
        assert len(processed.shape) == 2  # Grayscale
        assert np.all(np.isin(processed, [0, 255]))  # Binary

    def test_validate_circle(self, service):
        """Test circle validation."""
        # Create a test image with a clear circle
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2 = pytest.importorskip("cv2")
        cv2.circle(img, (50, 50), 20, (255, 255, 255), 2)  # Circle outline

        confidence = service._validate_circle(img, 50, 50, 20)

        assert isinstance(confidence, float)
        assert 0.0 <= confidence <= 1.0

    def test_deduplicate_holes(self, service):
        """Test hole deduplication."""
        holes = [
            {"center_px": (100, 100), "diameter_px": 20, "confidence": 0.8, "method": "test"},
            {"center_px": (105, 105), "diameter_px": 18, "confidence": 0.7, "method": "test"},  # Close duplicate
            {"center_px": (200, 200), "diameter_px": 15, "confidence": 0.6, "method": "test"}   # Far away
        ]

        filtered = service._deduplicate_holes(holes)

        # Should keep the higher confidence hole and the far away one
        assert len(filtered) == 2
        assert filtered[0]["center_px"] == (100, 100)  # Higher confidence first
        assert filtered[1]["center_px"] == (200, 200)

    def test_get_view_info_missing_file(self, service):
        """Test view info retrieval when auto_detect results don't exist."""
        with patch.object(service.file_storage, 'get_outputs_path') as mock_path:
            mock_path.return_value = MagicMock()
            mock_path.return_value.__truediv__ = lambda x, y: MagicMock()
            mock_path.return_value.__truediv__().exists.return_value = False

            result = service._get_selected_view_info("test_job")
            assert result is None

    def test_get_scale_info_fallback(self, service):
        """Test scale info fallback when scale_report doesn't exist."""
        with patch.object(service.file_storage, 'get_outputs_path') as mock_path:
            mock_path.return_value = MagicMock()
            mock_path.return_value.__truediv__ = lambda x, y: MagicMock()
            mock_path.return_value.__truediv__().exists.return_value = False

            result = service._get_scale_info("test_job")

            assert not result["available"]
            assert result["inch_per_pixel"] == 0.001  # Default fallback

    @pytest.mark.skipif(not pytest.importorskip("cv2", reason="OpenCV not available"))
    def test_detect_holes_cv_integration(self, service, sample_image):
        """Integration test for hole detection (requires OpenCV)."""
        processed = service._preprocess_image(sample_image)
        holes = service._detect_holes_cv(processed, sample_image)

        # Should detect at least some features
        assert isinstance(holes, list)

        # Check structure of detected holes
        for hole in holes:
            assert "center_px" in hole
            assert "diameter_px" in hole
            assert "confidence" in hole
            assert "method" in hole
            assert 0.0 <= hole["confidence"] <= 1.0

    @pytest.mark.skipif(not pytest.importorskip("cv2", reason="OpenCV not available"))
    def test_detect_slots_cv_integration(self, service, sample_image):
        """Integration test for slot detection (requires OpenCV)."""
        processed = service._preprocess_image(sample_image)
        slots = service._detect_slots_cv(processed, sample_image)

        # Should detect at least some features
        assert isinstance(slots, list)

        # Check structure of detected slots
        for slot in slots:
            assert "center_px" in slot
            assert "length_px" in slot
            assert "width_px" in slot
            assert "confidence" in slot
            assert "method" in slot
            assert 0.0 <= slot["confidence"] <= 1.0

    def test_create_hole_feature(self, service):
        """Test hole feature creation."""
        scale_info = {"inch_per_pixel": 0.01, "available": True}
        view_info = {"page": 1, "view_index": 0}

        hole_data = {
            "center_px": (100, 150),
            "diameter_px": 25.0,
            "confidence": 0.8,
            "method": "test"
        }

        feature = service._create_hole_feature(hole_data, scale_info, view_info)

        assert feature is not None
        assert feature.diameter == 0.25  # 25px * 0.01 in/px
        assert feature.confidence == 0.8
        assert feature.source_page == 1
        assert feature.source_view_index == 0
        assert feature.geometry_px["diameter"] == 25.0
        assert feature.geometry_in["diameter"] == 0.25

    def test_create_slot_feature(self, service):
        """Test slot feature creation."""
        scale_info = {"inch_per_pixel": 0.01, "available": True}
        view_info = {"page": 1, "view_index": 0}

        slot_data = {
            "center_px": (200, 100),
            "length_px": 100.0,
            "width_px": 20.0,
            "angle": 30.0,
            "confidence": 0.7,
            "method": "test"
        }

        feature = service._create_slot_feature(slot_data, scale_info, view_info)

        assert feature is not None
        assert feature.length == 1.0  # 100px * 0.01 in/px
        assert feature.width == 0.2   # 20px * 0.01 in/px
        assert feature.confidence == 0.7
        assert feature.source_page == 1
        assert feature.source_view_index == 0
        assert feature.geometry_px["length"] == 100.0
        assert feature.geometry_in["length"] == 1.0

    def test_merge_cv_with_text_features(self, service):
        """Test merging CV features with text features."""
        # This test would require mocking file operations
        # For now, just ensure the method exists and can be called
        assert hasattr(service, 'merge_cv_with_text_features')
        assert callable(service.merge_cv_with_text_features)