'use client';

/**
 * Sandboxed PDF viewer using react-pdf (pdf.js wrapper).
 *
 * Security:
 * - pdf.js runs in a Web Worker, which has no DOM access. The worker
 *   only processes PDF bytes and returns rendered canvas data — it
 *   can't execute arbitrary JS even if the PDF contains embedded
 *   JavaScript streams.
 * - pdf.js is configured to NOT load external resources (fonts,
 *   images) — only local cMap and standard font data from the same
 *   origin.
 * - The worker is loaded from the same origin (Next.js public dir)
 *   to prevent cross-origin worker injection.
 *
 * Performance:
 * - Pages are lazy-loaded: only the first 20 pages render initially,
 *   more load on scroll. Large PDFs (100+ pages) don't crash the tab.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';

// Configure the pdf.js worker. In Next.js, the worker file must be
// served from the public dir.
//
// SECURITY: this URL is same-origin (served from /public), not a CDN.
// A CDN URL would be a supply-chain risk — if the CDN were compromised,
// an attacker could inject a malicious worker.
pdfjs.GlobalWorkerOptions.workerSrc = `/pdf.worker.min.mjs`;

const MAX_INITIAL_PAGES = 20;

interface PdfViewerProps {
  /** The doc_id (hash or arxiv-{id}) used to fetch the PDF. */
  docId: string;
}

export function PdfViewer({ docId }: PdfViewerProps) {
  const [numPages, setNumPages] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visiblePages, setVisiblePages] = useState(MAX_INITIAL_PAGES);
  const containerRef = useRef<HTMLDivElement>(null);

  const pdfUrl = `/api/v1/papers/${docId}/pdf`;

  const onDocumentLoadSuccess = useCallback(({ numPages }: { numPages: number }) => {
    setNumPages(numPages);
    setError(null);
  }, []);

  const onDocumentLoadError = useCallback((err: Error) => {
    setError(err.message || 'Failed to load PDF');
  }, []);

  // Load more pages when the user scrolls near the bottom.
  const handleScroll = useCallback(() => {
    if (!containerRef.current || !numPages) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    if (scrollTop + clientHeight > scrollHeight - 500 && visiblePages < numPages) {
      setVisiblePages((prev) => Math.min(prev + 10, numPages));
    }
  }, [numPages, visiblePages]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.addEventListener('scroll', handleScroll);
    return () => container.removeEventListener('scroll', handleScroll);
  }, [handleScroll]);

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          <p className="mt-2 text-xs text-gray-500">
            The PDF could not be loaded. The file may be corrupted or the
            backend may be unreachable.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="h-full overflow-auto bg-gray-100 dark:bg-gray-900"
      data-testid="pdf-viewer"
    >
      <Document
        file={pdfUrl}
        onLoadSuccess={onDocumentLoadSuccess}
        onLoadError={onDocumentLoadError}
        loading={
          <div className="flex h-96 items-center justify-center">
            <p className="text-sm text-gray-500">Loading PDF…</p>
          </div>
        }
        options={{
          // Use standard font data from the same origin (public dir).
          standardFontDataUrl: '/fonts/',
          cMapUrl: '/cmaps/',
          cMapPacked: true,
        }}
      >
        {Array.from({ length: Math.min(visiblePages, numPages ?? 0) }, (_, i) => (
          <Page
            key={`page-${i + 1}`}
            pageNumber={i + 1}
            width={600}
            className="mx-auto mb-4 shadow-md"
            renderTextLayer={true}
            renderAnnotationLayer={false}
          />
        ))}
      </Document>

      {numPages && visiblePages < numPages && (
        <div className="py-4 text-center text-sm text-gray-500">
          Showing {visiblePages} of {numPages} pages. Scroll to load more.
        </div>
      )}
    </div>
  );
}
