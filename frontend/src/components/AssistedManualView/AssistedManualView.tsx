import { useState, useEffect } from 'react';
import { api } from '../../services/api';
import './AssistedManualView.css';

interface View {
  bbox: [number, number, number, number]; // Normalized [x_min, y_min, x_max, y_max]
  bbox_pixels: [number, number, number, number]; // [x, y, width, height]
  area: number;
  confidence: number;
}

interface PageData {
  page: number;
  views: View[];
  image_size: [number, number];
}

interface AssistedManualViewProps {
  jobId: string;
  onViewSelected?: (page: number, viewIndex: number) => void;
}

function AssistedManualView({ jobId, onViewSelected }: AssistedManualViewProps) {
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [pageImages, setPageImages] = useState<string[]>([]);
  const [pageData, setPageData] = useState<PageData[]>([]);
  const [selectedView, setSelectedView] = useState<{ page: number; viewIndex: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
        setPdfFile(file);
        setError(null);
      } else {
        setError('Please select a PDF file');
      }
    }
  };

  const handleUpload = async () => {
    if (!pdfFile) {
      setError('Please select a PDF file');
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const result = await api.uploadPdf(jobId, pdfFile);
      setPageImages(result.page_images);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload PDF');
    } finally {
      setUploading(false);
    }
  };

  const handleDetectViews = async () => {
    if (pageImages.length === 0) {
      setError('Please upload PDF first');
      return;
    }

    setDetecting(true);
    setError(null);

    try {
      const result = await api.detectViews(jobId);
      setPageData(result.pages);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to detect views');
    } finally {
      setDetecting(false);
    }
  };

  const handleViewClick = async (page: number, viewIndex: number) => {
    setSelectedView({ page, viewIndex });
    
    // Save to job state
    try {
      await api.saveSelectedView(jobId, page, viewIndex);
    } catch (err) {
      console.error('Failed to save selected view:', err);
      // Don't show error to user, just log it
    }
    
    if (onViewSelected) {
      onViewSelected(page, viewIndex);
    }
  };

  // Load existing page images and views if available
  useEffect(() => {
    const loadExisting = async () => {
      try {
        const files = await api.getJobFiles(jobId);
        const pageImageFiles = files.files
          .filter((f) => f.path.startsWith('outputs/pdf_pages/'))
          .sort((a, b) => {
            const aNum = parseInt(a.name.match(/page_(\d+)/)?.[1] || '0');
            const bNum = parseInt(b.name.match(/page_(\d+)/)?.[1] || '0');
            return aNum - bNum;
          });

        if (pageImageFiles.length > 0) {
          setPageImages(pageImageFiles.map((f) => f.path));

          // Try to load views
          try {
            const viewsResult = await api.detectViews(jobId);
            setPageData(viewsResult.pages);
          } catch {
            // Views not detected yet, that's OK
          }
        }
      } catch {
        // No existing files, that's OK
      }
    };

    loadExisting();
  }, [jobId]);

  return (
    <div className="assisted-manual-view">
      <div className="upload-section">
        <h2>Upload PDF</h2>
        <div className="upload-controls">
          <input
            type="file"
            accept=".pdf,application/pdf"
            onChange={handleFileChange}
            disabled={uploading}
            className="file-input"
          />
          <button
            onClick={handleUpload}
            disabled={!pdfFile || uploading}
            className="upload-button"
          >
            {uploading ? 'Uploading...' : 'Upload PDF'}
          </button>
          {pageImages.length > 0 && (
            <button
              onClick={handleDetectViews}
              disabled={detecting}
              className="detect-button"
            >
              {detecting ? 'Detecting...' : 'Detect Views'}
            </button>
          )}
        </div>
        {error && <div className="error-message">{error}</div>}
      </div>

      {pageImages.length > 0 && (
        <div className="pages-section">
          <h2>Page Thumbnails</h2>
          <div className="pages-grid">
            {pageImages.map((imagePath, index) => {
              const pageNum = parseInt(imagePath.match(/page_(\d+)/)?.[1] || '0');
              const pageViews = pageData.find((p) => p.page === pageNum);

              return (
                <PageThumbnail
                  key={index}
                  jobId={jobId}
                  pageNum={pageNum}
                  imagePath={imagePath}
                  views={pageViews?.views || []}
                  imageSize={pageViews?.image_size || [0, 0]}
                  selectedView={
                    selectedView?.page === pageNum ? selectedView.viewIndex : null
                  }
                  onViewClick={(viewIndex) => handleViewClick(pageNum, viewIndex)}
                />
              );
            })}
          </div>
        </div>
      )}

      {selectedView && (
        <div className="selected-view-info">
          <h3>Selected View</h3>
          <p>
            Page {selectedView.page}, View {selectedView.viewIndex + 1}
          </p>
        </div>
      )}
    </div>
  );
}

interface PageThumbnailProps {
  jobId: string;
  pageNum: number;
  imagePath: string;
  views: View[];
  imageSize: [number, number];
  selectedView: number | null;
  onViewClick: (viewIndex: number) => void;
}

function PageThumbnail({
  jobId,
  pageNum,
  imagePath,
  views,
  imageSize,
  selectedView,
  onViewClick,
}: PageThumbnailProps) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    const url = api.getPdfUrl(jobId, imagePath);
    setImageUrl(url);
  }, [jobId, imagePath]);

  const handleViewRectClick = (e: React.MouseEvent, viewIndex: number) => {
    e.stopPropagation();
    onViewClick(viewIndex);
  };

  if (!imageUrl) {
    return <div className="page-thumbnail loading">Loading...</div>;
  }

  const [imgWidth, imgHeight] = imageSize;
  const aspectRatio = imgWidth > 0 && imgHeight > 0 ? imgWidth / imgHeight : 1;

  return (
    <div className="page-thumbnail">
      <div className="page-thumbnail-header">Page {pageNum}</div>
      <div
        className="page-thumbnail-container"
        style={{ aspectRatio }}
      >
        <img src={imageUrl} alt={`Page ${pageNum}`} className="page-image" />
        <svg
          className="view-overlay"
          viewBox={`0 0 ${imgWidth || 1} ${imgHeight || 1}`}
          preserveAspectRatio="none"
        >
          {views.map((view, index) => {
            const [x, y, w, h] = view.bbox_pixels;
            const isSelected = selectedView === index;

            return (
              <rect
                key={index}
                x={x}
                y={y}
                width={w}
                height={h}
                className={`view-rect ${isSelected ? 'selected' : ''}`}
                onClick={(e) => handleViewRectClick(e, index)}
                style={{ cursor: 'pointer' }}
              />
            );
          })}
        </svg>
      </div>
      {views.length > 0 && (
        <div className="page-thumbnail-footer">
          {views.length} view{views.length !== 1 ? 's' : ''} detected
        </div>
      )}
    </div>
  );
}

export default AssistedManualView;

