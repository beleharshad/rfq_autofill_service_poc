import { useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/esm/Page/AnnotationLayer.css';
import 'react-pdf/dist/esm/Page/TextLayer.css';
import './PDFViewer.css';

// Set up PDF.js worker
pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.js`;

interface PDFViewerProps {
  url: string;
}

function PDFViewer({ url }: PDFViewerProps) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [scale, setScale] = useState(1.0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }) {
    setNumPages(numPages);
    setLoading(false);
    setError(null);
  }

  function onDocumentLoadError(error: Error) {
    setError(`Failed to load PDF: ${error.message}`);
    setLoading(false);
  }

  function goToPrevPage() {
    setPageNumber((prev) => Math.max(1, prev - 1));
  }

  function goToNextPage() {
    setPageNumber((prev) => Math.min(numPages || 1, prev + 1));
  }

  function handleZoomIn() {
    setScale((prev) => Math.min(3.0, prev + 0.25));
  }

  function handleZoomOut() {
    setScale((prev) => Math.max(0.5, prev - 0.25));
  }

  function handleZoomReset() {
    setScale(1.0);
  }

  function copyPageNumber() {
    navigator.clipboard.writeText(pageNumber.toString());
    // You could add a toast notification here
    alert(`Page ${pageNumber} copied to clipboard`);
  }

  return (
    <div className="pdf-viewer">
      <div className="pdf-controls">
        <div className="pdf-controls-left">
          <button onClick={goToPrevPage} disabled={pageNumber <= 1}>
            ‹ Prev
          </button>
          <span className="page-info">
            Page{' '}
            <input
              type="number"
              min={1}
              max={numPages || 1}
              value={pageNumber}
              onChange={(e) => {
                const page = parseInt(e.target.value, 10);
                if (page >= 1 && page <= (numPages || 1)) {
                  setPageNumber(page);
                }
              }}
              className="page-input"
            />{' '}
            of {numPages || '?'}
          </span>
          <button onClick={goToNextPage} disabled={pageNumber >= (numPages || 1)}>
            Next ›
          </button>
        </div>
        <div className="pdf-controls-center">
          <button onClick={handleZoomOut}>−</button>
          <span className="zoom-info">{Math.round(scale * 100)}%</span>
          <button onClick={handleZoomIn}>+</button>
          <button onClick={handleZoomReset}>Reset</button>
        </div>
        <div className="pdf-controls-right">
          <button onClick={copyPageNumber}>Copy Page #{pageNumber}</button>
        </div>
      </div>

      <div className="pdf-container">
        {loading && <div className="pdf-loading">Loading PDF...</div>}
        {error && <div className="pdf-error">{error}</div>}
        {!error && (
          <Document
            file={url}
            onLoadSuccess={onDocumentLoadSuccess}
            onLoadError={onDocumentLoadError}
            loading={<div className="pdf-loading">Loading PDF...</div>}
          >
            <Page
              pageNumber={pageNumber}
              scale={scale}
              renderTextLayer={true}
              renderAnnotationLayer={true}
            />
          </Document>
        )}
      </div>
    </div>
  );
}

export default PDFViewer;








