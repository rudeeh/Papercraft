'use client';

/**
 * Split-screen paper detail page.
 *
 * Left:  PDF viewer (react-pdf / pdf.js)
 * Right: Extraction panel (sanitized Markdown with sections, entities,
 *       citations, graph stats)
 *
 * Trust model:
 * - The PDF is the source of truth — the user can always compare the
 *   extracted output against the original document.
 * - The extraction panel labels S2 TLDRs with their provenance so users
 *   know which data is auto-generated vs. parsed from the PDF.
 * - If Neo4j is down, the PDF is still viewable; the right panel shows
 *   a "graph data unavailable" message instead of failing.
 */

import { useState, useEffect } from 'react';
import { useParams } from 'next/navigation';
import { PdfViewer } from '@/components/PdfViewer';
import { ExtractionPanel } from '@/components/ExtractionPanel';
import { paperApi, type PaperExtraction } from '@/lib/api';

export default function PaperDetailPage() {
  const params = useParams<{ doc_id: string }>();
  const docId = params.doc_id;

  const [extraction, setExtraction] = useState<PaperExtraction | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!docId) return;

    let cancelled = false;
    setLoading(true);
    setError(null);

    paperApi
      .extraction(docId)
      .then((data) => {
        if (!cancelled) {
          setExtraction(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to load extraction data');
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [docId]);

  if (!docId) {
    return (
      <div className="flex h-screen items-center justify-center">
        <p className="text-sm text-gray-500">No document ID provided.</p>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3 dark:border-gray-700 dark:bg-gray-900">
        <div>
          <h1 className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            {extraction?.title || `Paper ${docId}`}
          </h1>
          {extraction?.extraction_available && (
            <p className="text-xs text-gray-500">
              Split-screen view · PDF ↔ extracted data
            </p>
          )}
        </div>
        <a
          href="/"
          className="text-xs text-blue-600 hover:underline dark:text-blue-400"
        >
          ← Back to ingest
        </a>
      </div>

      {/* Split-screen layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: PDF viewer */}
        <div className="flex-1 border-r border-gray-200 dark:border-gray-700">
          <PdfViewer docId={docId} />
        </div>

        {/* Right: Extraction panel */}
        <div className="flex-1 bg-white dark:bg-gray-900">
          <ExtractionPanel
            extraction={extraction}
            loading={loading}
            error={error}
          />
        </div>
      </div>
    </div>
  );
}
