"""
Context Builder Module (Phase 15.1)

Builds the LLM prompt context from a ``HybridRetriever`` result (Phase 13):
graph facts, text evidence, citation paths, and source paper metadata. The
output is both a single text block ready to drop into a prompt, and the
structured pieces that went into it, so the answer generator (Phase 15.2)
can ground and cross-check the model's answer against the same data.
"""

from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class ContextBuilder:
    """Builds grounded LLM context from hybrid retrieval results."""

    def build(self, retrieval_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parameters
        ----------
        retrieval_result : the dict returned by ``HybridRetriever.retrieve()``
            (or any dict with the same ``graph_facts`` / ``vector_results`` /
            ``citation_paths`` shape).

        Returns
        -------
        {
          "context_text": str,        # ready to drop into an LLM prompt
          "graph_facts": [...],       # unchanged, for grounding checks
          "text_evidence": [...],     # unchanged vector_results
          "citation_paths": [...],
          "source_papers": [...],     # deduped {"paper_id", "title"}
        }
        """
        graph_facts = retrieval_result.get("graph_facts") or []
        vector_results = retrieval_result.get("vector_results") or []
        citation_paths = retrieval_result.get("citation_paths") or []

        source_papers = self._collect_source_papers(
            graph_facts, vector_results, citation_paths,
        )

        sections: List[str] = []
        if graph_facts:
            sections.append(self._render_graph_facts(graph_facts))
        if vector_results:
            sections.append(self._render_text_evidence(vector_results))
        if citation_paths:
            sections.append(self._render_citation_paths(citation_paths))
        if source_papers:
            sections.append(self._render_source_papers(source_papers))

        return {
            "context_text": "\n\n".join(sections),
            "graph_facts": graph_facts,
            "text_evidence": vector_results,
            "citation_paths": citation_paths,
            "source_papers": source_papers,
        }

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render_graph_facts(facts: List[Dict[str, Any]]) -> str:
        lines = ["Graph facts:"]
        for f in facts:
            subject = (f.get("subject") or {}).get("name", "?")
            obj = (f.get("object") or {}).get("name", "?")
            relation = f.get("relation") or "RELATED_TO"
            line = f"- {subject} {relation} {obj}"
            if f.get("evidence"):
                line += f' (evidence: "{f["evidence"]}")'
            pids = f.get("source_paper_ids") or []
            if pids:
                line += f" [source: {', '.join(pids)}]"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_text_evidence(results: List[Dict[str, Any]]) -> str:
        lines = ["Text evidence:"]
        for r in results:
            snippet = (r.get("text") or "").strip()
            pid = r.get("paper_id") or "unknown"
            section = r.get("section")
            location = f"{pid}" + (f" / {section}" if section else "")
            lines.append(f'- ({location}) "{snippet}"')
        return "\n".join(lines)

    @staticmethod
    def _render_citation_paths(paths: List[Dict[str, Any]]) -> str:
        lines = ["Citation paths:"]
        for cp in paths:
            chain = " -> ".join(cp.get("path") or [])
            lines.append(
                f"- {chain} (depth {cp.get('depth')}, {cp.get('direction')})"
            )
        return "\n".join(lines)

    @staticmethod
    def _render_source_papers(papers: List[Dict[str, Any]]) -> str:
        lines = ["Source papers:"]
        for p in papers:
            title = p.get("title") or p["paper_id"]
            lines.append(f"- [{p['paper_id']}] {title}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Source paper collection
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_source_papers(
        graph_facts: List[Dict[str, Any]],
        vector_results: List[Dict[str, Any]],
        citation_paths: List[Dict[str, Any]],
    ) -> List[Dict[str, Optional[str]]]:
        papers: Dict[str, Dict[str, Optional[str]]] = {}

        def add(paper_id: Optional[str], title: Optional[str] = None) -> None:
            if not paper_id:
                return
            if paper_id not in papers:
                papers[paper_id] = {"paper_id": paper_id, "title": title}
            elif title and not papers[paper_id].get("title"):
                papers[paper_id]["title"] = title

        for fact in graph_facts:
            for node in (fact.get("subject"), fact.get("object")):
                if node and node.get("type") == "Paper":
                    add(node.get("paper_id"), node.get("name"))
            for pid in fact.get("source_paper_ids") or []:
                add(pid)

        for result in vector_results:
            add(result.get("paper_id"))

        for cp in citation_paths:
            add(cp.get("paper_id"), cp.get("title"))

        return sorted(papers.values(), key=lambda p: p["paper_id"])
