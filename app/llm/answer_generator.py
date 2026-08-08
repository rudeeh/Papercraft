"""
Answer Generator Module (Phase 15.2)

Generates a grounded answer from hybrid-retrieval context (Phase 15.1),
using the existing LLM service (``app/services/llm.py``). The system prompt
instructs the model to answer using only the given context and to cite
source papers exactly as listed there -- never invent a citation.

Grounding is also enforced at the code level, not just via the prompt: if
retrieval found nothing, the generator refuses to answer rather than
calling the LLM (which is the one case where "don't invent citations" can
be guaranteed rather than merely requested).

Output format:
{
  "answer": str,
  "sources": [{"paper_id", "title"}],
  "graph_facts_used": [...],
  "confidence_notes": [...],
}
"""

from typing import Any, Dict, List, Optional

import structlog

from app.llm.context_builder import ContextBuilder

logger = structlog.get_logger()


SYSTEM_PROMPT = (
    "You are a research assistant answering questions about academic papers. "
    "Answer ONLY using the facts and evidence given in the context below -- "
    "every claim must be traceable to it. "
    "Cite source papers using their paper_id exactly as given in the context's "
    "'Source papers' list. Never invent a paper_id, title, or citation that is "
    "not present in the context. "
    "If the context does not contain enough information to answer, say so "
    "explicitly instead of guessing."
)

NO_CONTEXT_ANSWER = (
    "I don't have enough retrieved context to answer this question. Try "
    "rephrasing, or make sure relevant papers have been ingested."
)


class AnswerGenerator:
    """Generates a grounded answer from hybrid-retrieval context."""

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        context_builder: Optional[ContextBuilder] = None,
    ) -> None:
        if llm_client is None:
            from app.services.llm import llm_client as default_client
            llm_client = default_client
        self._llm = llm_client
        self._context_builder = context_builder or ContextBuilder()

    def generate(
        self,
        query: str,
        retrieval_result: Dict[str, Any],
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Parameters
        ----------
        query : the user's original natural-language question
        retrieval_result : output of ``HybridRetriever.retrieve()``
        api_key : optional OpenRouter API key to use for this call instead
            of the server-configured one (e.g. supplied by the caller when
            no server key is configured). Propagates
            ``app.services.llm.LLMNotConfiguredError`` if neither is set.

        Returns
        -------
        {"answer": str, "sources": [...], "graph_facts_used": [...],
         "confidence_notes": [...]}
        """
        context = self._context_builder.build(retrieval_result)
        confidence_notes = self._confidence_notes(context)

        if not context["context_text"].strip():
            answer = NO_CONTEXT_ANSWER
        else:
            prompt = self._build_prompt(query, context["context_text"])
            answer = self._llm.generate_response(prompt, system_prompt=SYSTEM_PROMPT, api_key=api_key)

        logger.info(
            "answer_generated",
            query=query,
            sources=len(context["source_papers"]),
            graph_facts=len(context["graph_facts"]),
            has_context=bool(context["context_text"].strip()),
        )

        return {
            "answer": answer,
            "sources": context["source_papers"],
            "graph_facts_used": context["graph_facts"],
            "confidence_notes": confidence_notes,
        }

    @staticmethod
    def _build_prompt(query: str, context_text: str) -> str:
        return (
            f"Context:\n{context_text}\n\n"
            f"Question: {query}\n\n"
            "Answer the question using only the context above."
        )

    @staticmethod
    def _confidence_notes(context: Dict[str, Any]) -> List[str]:
        notes: List[str] = []
        if not context["source_papers"]:
            notes.append("No source papers were retrieved for this query.")
        if not context["graph_facts"] and not context["text_evidence"]:
            notes.append(
                "No graph facts or text evidence were retrieved; "
                "answer may be unreliable."
            )
        return notes
