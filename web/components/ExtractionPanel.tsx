'use client';

/**
 * Extraction panel — renders the parsed extraction data as sanitized
 * Markdown.
 *
 * Security:
 * - react-markdown with rehype-sanitize strips ALL HTML tags from the
 *   rendered output. Only Markdown syntax (headings, bold, lists, etc.)
 *   is allowed. This prevents XSS from PDF-extracted text that might
 *   contain <script> tags or other HTML.
 * - No dangerouslySetInnerHTML anywhere.
 * - The TLDR from Semantic Scholar is labeled with its provenance
 *   (model + source) so users know it's auto-generated, not ground truth.
 */

import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';
import type { PaperExtraction } from '@/lib/api';

interface ExtractionPanelProps {
  extraction: PaperExtraction | null;
  loading: boolean;
  error: string | null;
}

export function ExtractionPanel({ extraction, loading, error }: ExtractionPanelProps) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-gray-500">Loading extraction…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          <p className="mt-2 text-xs text-gray-500">
            The extraction data could not be loaded. The PDF is still
            viewable on the left.
          </p>
        </div>
      </div>
    );
  }

  if (!extraction) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <p className="text-sm text-gray-500">No extraction data available.</p>
      </div>
    );
  }

  if (!extraction.extraction_available) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center">
        <div>
          <p className="text-sm text-gray-500">
            Graph data is unavailable for this paper.
          </p>
          <p className="mt-2 text-xs text-gray-400">
            This usually means the ingestion pipeline hasn&apos;t completed
            yet, or Neo4j is not running. The PDF is still viewable on
            the left.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6" data-testid="extraction-panel">
      {/* Title */}
      {extraction.title && (
        <h1 className="mb-2 text-xl font-bold text-gray-900 dark:text-gray-100">
          {extraction.title}
        </h1>
      )}

      {/* Abstract */}
      {extraction.abstract && (
        <div className="mb-6">
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-gray-500">
            Abstract
          </h2>
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
              {extraction.abstract}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Sections */}
      {extraction.sections.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
            Parsed Sections
          </h2>
          <div className="space-y-4">
            {extraction.sections.map((section, i) => (
              <div key={`section-${i}`}>
                <h3 className="text-base font-semibold text-gray-800 dark:text-gray-200">
                  {section.heading}
                </h3>
                <div className="prose prose-sm dark:prose-invert mt-1 max-w-none">
                  <ReactMarkdown rehypePlugins={[rehypeSanitize]}>
                    {section.text}
                  </ReactMarkdown>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Entities */}
      {extraction.entities.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
            Extracted Entities ({extraction.entities.length})
          </h2>
          <div className="flex flex-wrap gap-2">
            {extraction.entities.map((entity, i) => (
              <span
                key={`entity-${i}`}
                className="inline-flex items-center rounded-md bg-blue-50 px-2.5 py-0.5 text-xs font-medium text-blue-700 dark:bg-blue-900/30 dark:text-blue-300"
              >
                <span className="mr-1 text-blue-400">{entity.node_type}</span>
                {entity.name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Citations */}
      {extraction.citations.length > 0 && (
        <div className="mb-6">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
            Citations ({extraction.citations.length})
          </h2>
          <div className="space-y-2">
            {extraction.citations.slice(0, 20).map((citation, i) => (
              <div
                key={`citation-${i}`}
                className="rounded-md border border-gray-200 p-3 dark:border-gray-700"
              >
                {citation.title && (
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-200">
                    {citation.title}
                  </p>
                )}
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-gray-500">
                  {citation.doi && <span>DOI: {citation.doi}</span>}
                  {citation.arxiv_id && <span>arXiv: {citation.arxiv_id}</span>}
                  {citation.citation_count !== undefined && (
                    <span>Cited by: {citation.citation_count}</span>
                  )}
                  {citation.influential_citation_count !== undefined &&
                    citation.influential_citation_count > 0 && (
                      <span className="font-medium text-blue-600 dark:text-blue-400">
                        Influential: {citation.influential_citation_count}
                      </span>
                    )}
                  {citation.is_stub && (
                    <span className="text-amber-600 dark:text-amber-400">
                      (stub — not yet ingested)
                    </span>
                  )}
                </div>
                {citation.tldr && (
                  <div className="mt-2 rounded bg-gray-50 p-2 dark:bg-gray-800">
                    <p className="text-xs text-gray-600 dark:text-gray-400">
                      {citation.tldr.text}
                    </p>
                    <p className="mt-1 text-[10px] text-gray-400">
                      S2 TLDR (auto-generated by {citation.tldr.model})
                    </p>
                  </div>
                )}
                {citation.abstract && !citation.tldr && (
                  <p className="mt-1 text-xs text-gray-500 line-clamp-3">
                    {citation.abstract}
                  </p>
                )}
              </div>
            ))}
            {extraction.citations.length > 20 && (
              <p className="text-xs text-gray-400">
                + {extraction.citations.length - 20} more citations not shown
              </p>
            )}
          </div>
        </div>
      )}

      {/* Graph stats */}
      {Object.keys(extraction.graph_stats).length > 0 && (
        <div className="border-t border-gray-200 pt-4 dark:border-gray-700">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">
            Graph Stats
          </h2>
          <div className="grid grid-cols-3 gap-4 text-center">
            {Object.entries(extraction.graph_stats).map(([key, value]) => (
              <div key={key}>
                <p className="text-lg font-bold text-gray-800 dark:text-gray-200">
                  {value}
                </p>
                <p className="text-xs text-gray-500">
                  {key.replace(/_/g, ' ')}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
