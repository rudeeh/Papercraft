"""
LLM-backed entity and relation extraction (README Phase 1.2).

The deterministic extractors in ``entity_extractor.py`` /
``relation_extractor.py`` are fast, free and reproducible, but they only
find what their regexes and known-term lists already know about. This
module runs the two-stage LLM pipeline the README specifies -- entities
first, then relations *given* those entities -- and emits the same dict
shape the heuristic extractors do, plus a ``confidence`` the router uses.

Three things this module refuses to do, because they are how LLM
extraction quietly corrupts a graph:

1. **Invent ontology terms.** Any entity whose ``type`` or relation whose
   ``relation`` is not in the ontology is dropped, not coerced.
2. **Assert without evidence.** Every extraction must quote a span; ones
   that quote text not present in the source are dropped, which is the
   cheapest available hallucination check.
3. **Fail loudly.** A model timeout, a refusal, or unparseable output
   returns an empty list. Ingestion continues on the heuristic path.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

import structlog

from app.core.config import settings
from app.graph.ontology import EdgeType, NodeType, OntologyValidator
from app.services.llm import LLMClient, LLMNotConfiguredError

logger = structlog.get_logger()

EXTRACTABLE_ENTITY_TYPES = ["Method", "Dataset", "Task", "Metric", "Claim", "Experiment"]

# Cap on how much of a paper goes into one prompt. Papers routinely exceed
# any context window once you include the prompt scaffolding, and the
# entity-dense parts (abstract, intro, results) come first anyway.
MAX_PROMPT_CHARS = 12_000

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

ENTITY_SYSTEM_PROMPT = """You extract structured research entities from academic papers.

Return ONLY a JSON array. No prose, no markdown fence, no explanation.

Each element must be an object with exactly these keys:
  "name"            - the entity as written in the paper
  "type"            - one of: Method, Dataset, Task, Metric, Claim, Experiment
  "source_section"  - the heading the entity appeared under
  "evidence"        - a VERBATIM sentence from the text containing the entity
  "confidence"      - your confidence this is a real, correctly-typed entity, 0.0-1.0

Rules:
- "evidence" must be copied character-for-character from the input. Never paraphrase it.
- If you are unsure of the type, omit the entity entirely rather than guessing.
- Do not invent entities that are only implied. Extract what is stated.
- Return [] if the text contains no extractable entities."""

RELATION_SYSTEM_PROMPT = """You extract relationships between research entities already identified in a paper.

Return ONLY a JSON array. No prose, no markdown fence, no explanation.

Each element must be an object with exactly these keys:
  "source"       - name of the source entity, exactly as given to you
  "source_type"  - its type, exactly as given to you
  "relation"     - one of the allowed relation types listed in the user message
  "target"       - name of the target entity, exactly as given to you
  "target_type"  - its type, exactly as given to you
  "evidence"     - a VERBATIM sentence from the text stating this relationship
  "confidence"   - your confidence this relationship is stated in the text, 0.0-1.0

Rules:
- Only relate entities from the provided list. Never introduce new ones.
- "evidence" must be copied character-for-character from the input.
- Only extract relationships the text actually states. Plausible is not enough.
- Return [] if no relationships are stated."""


class LLMEntityRelationExtractor:
    """Two-stage LLM extractor emitting ontology-valid, evidence-backed dicts."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        model_version: Optional[str] = None,
        max_prompt_chars: int = MAX_PROMPT_CHARS,
    ) -> None:
        self._llm = llm_client or LLMClient()
        self.model_version = model_version or settings.EXTRACTION_MODEL_VERSION
        self.max_prompt_chars = max_prompt_chars

    # ------------------------------------------------------------------
    # Stage 1: entities
    # ------------------------------------------------------------------

    def extract_entities(
        self, text: str, *, api_key: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        source = (text or "").strip()
        if not source:
            return []

        prompt = (
            "Extract research entities from the following paper text.\n\n"
            f"--- PAPER TEXT ---\n{source[: self.max_prompt_chars]}\n--- END ---"
        )
        raw = self._complete(prompt, ENTITY_SYSTEM_PROMPT, api_key=api_key)
        if raw is None:
            return []

        return [
            entity
            for entity in (self._clean_entity(item, source) for item in _parse_json_array(raw))
            if entity is not None
        ]

    # ------------------------------------------------------------------
    # Stage 2: relations
    # ------------------------------------------------------------------

    def extract_relations(
        self,
        entities: Sequence[Dict[str, Any]],
        text: str,
        *,
        api_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        source = (text or "").strip()
        # One entity can't be related to anything, so the call would be
        # pure cost with a guaranteed empty result.
        if not source or len(entities) < 2:
            return []

        listing = "\n".join(
            f"- {e.get('name')} ({e.get('type')})" for e in entities if e.get("name") and e.get("type")
        )
        allowed = ", ".join(sorted({e.value for e in EdgeType}))
        prompt = (
            f"Entities found in this paper:\n{listing}\n\n"
            f"Allowed relation types: {allowed}\n\n"
            "Extract the relationships stated between these entities.\n\n"
            f"--- PAPER TEXT ---\n{source[: self.max_prompt_chars]}\n--- END ---"
        )
        raw = self._complete(prompt, RELATION_SYSTEM_PROMPT, api_key=api_key)
        if raw is None:
            return []

        known = {
            (e.get("name") or "").casefold(): e.get("type")
            for e in entities
            if e.get("name")
        }
        return [
            relation
            for relation in (
                self._clean_relation(item, source, known) for item in _parse_json_array(raw)
            )
            if relation is not None
        ]

    def extract(
        self, text: str, *, api_key: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Run both stages; relations are extracted over stage 1's entities."""
        entities = self.extract_entities(text, api_key=api_key)
        relations = self.extract_relations(entities, text, api_key=api_key)
        return {"entities": entities, "relations": relations}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _complete(
        self, prompt: str, system_prompt: str, *, api_key: Optional[str]
    ) -> Optional[str]:
        try:
            return self._llm.generate_response(prompt, system_prompt=system_prompt, api_key=api_key)
        except LLMNotConfiguredError:
            logger.info("llm_extraction_skipped_no_api_key")
            return None
        except Exception as exc:
            logger.warning("llm_extraction_call_failed", error=str(exc))
            return None

    def _clean_entity(self, item: Any, source: str) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None

        name = _clean_str(item.get("name"))
        entity_type = _clean_str(item.get("type"))
        evidence = _clean_str(item.get("evidence"))

        if not name or not entity_type or not evidence:
            return None
        if entity_type not in EXTRACTABLE_ENTITY_TYPES:
            return None
        if not OntologyValidator.validate_node_type(entity_type):
            return None
        if not _evidence_is_grounded(evidence, source):
            logger.debug("llm_entity_dropped_ungrounded_evidence", name=name)
            return None

        return {
            "name": name,
            "type": entity_type,
            "source_section": _clean_str(item.get("source_section")) or "",
            "evidence": evidence,
            "confidence": _clamp_confidence(item.get("confidence")),
            "extracted_by": self.model_version,
        }

    def _clean_relation(
        self, item: Any, source: str, known_entities: Dict[str, Optional[str]]
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None

        src = _clean_str(item.get("source"))
        tgt = _clean_str(item.get("target"))
        relation = _clean_str(item.get("relation"))
        evidence = _clean_str(item.get("evidence"))

        if not src or not tgt or not relation or not evidence:
            return None
        if src.casefold() == tgt.casefold():
            return None  # Self-relations are always extraction noise.

        # Types come from stage 1, not from the model's second answer: if it
        # re-states a type it can contradict itself and produce an edge the
        # ontology would accept but the graph would mis-shape.
        src_type = known_entities.get(src.casefold())
        tgt_type = known_entities.get(tgt.casefold())
        if not src_type or not tgt_type:
            return None

        if not OntologyValidator.validate_edge(src_type, relation, tgt_type):
            logger.debug(
                "llm_relation_dropped_invalid_edge",
                edge=f"{src_type} -{relation}-> {tgt_type}",
            )
            return None
        if not _evidence_is_grounded(evidence, source):
            logger.debug("llm_relation_dropped_ungrounded_evidence", source=src, target=tgt)
            return None

        return {
            "source": src,
            "source_type": src_type,
            "relation": relation,
            "target": tgt,
            "target_type": tgt_type,
            "evidence": evidence,
            "confidence": _clamp_confidence(item.get("confidence")),
            "extracted_by": self.model_version,
        }


# ======================================================================
# Parsing / validation helpers
# ======================================================================

def _parse_json_array(raw: str) -> List[Any]:
    """
    Pull a JSON array out of a model response.

    Models fence their JSON, prepend "Here is the JSON:", or both, however
    firmly the system prompt says not to -- so try the whole string, then a
    fenced block, then the outermost bracket pair.
    """
    if not raw:
        return []

    candidates = [raw.strip()]

    fenced = _JSON_BLOCK_RE.search(raw)
    if fenced:
        candidates.append(fenced.group(1).strip())

    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            return parsed
        # A single object where an array was asked for is a common slip and
        # unambiguous to recover from.
        if isinstance(parsed, dict):
            return [parsed]

    logger.warning("llm_extraction_unparseable_response", preview=raw[:200])
    return []


def _clean_str(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _clamp_confidence(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _evidence_is_grounded(evidence: str, source: str) -> bool:
    """
    Whether *evidence* actually occurs in *source*.

    Compared on whitespace- and case-normalised text because models
    reliably normalise spacing and line breaks when quoting, and the
    original is full of PDF-extraction line wrapping. Very short evidence
    is rejected outright: a five-character "quote" matches by accident.
    """
    if len(evidence) < 20:
        return False
    return _normalize_for_match(evidence) in _normalize_for_match(source)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()
