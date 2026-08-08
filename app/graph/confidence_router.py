"""
Confidence-based routing for extractions (README Phase 1.2 / Data Flow).

Every extraction lands in one of four buckets:

    confidence >= CONFIDENCE_AUTO_INSERT   -> AUTO_INSERT  (straight into Neo4j)
    confidence >= CONFIDENCE_DRAFT         -> DRAFT        (queued for attestation)
    confidence >  0                        -> MANUAL       (queued, flagged)
    confidence == 0 / missing              -> DISCARDED    (never stored)

Two properties this module is careful about:

- **Deterministic thresholds, not a model's self-report alone.** A raw LLM
  confidence is adjusted by signals we can check ourselves (does it have
  evidence? is the evidence substantial? did two extractors independently
  agree?) before it is compared against a threshold.
- **Heuristic extractions are not silently trusted.** The deterministic
  extractors carry no confidence field at all; they are assigned one, so a
  regex hit and a model hit are ranked on the same scale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import structlog

from app.core.config import settings
from app.db.models import DraftKind, RoutingDecision

logger = structlog.get_logger()

# Confidence assigned to a deterministic extraction that carries none.
# Above the default auto-insert threshold (0.85) on purpose: a regex match
# against a curated term list is reproducible and auditable -- re-running
# the extractor gives the same answer and a reviewer can read the rule that
# fired. That is a different kind of claim from a model's self-reported
# score, and it is also the pre-routing behaviour, so a heuristic-only
# deployment's graph is unchanged by this module's introduction.
HEURISTIC_BASE_CONFIDENCE = 0.90

# Bonus when both extractors independently produced the same item -- the
# strongest signal available short of a human.
AGREEMENT_BONUS = 0.15

# Penalty for an extraction whose evidence is too thin to review.
THIN_EVIDENCE_PENALTY = 0.20
MIN_SUBSTANTIAL_EVIDENCE_CHARS = 40


@dataclass(frozen=True)
class RoutedExtraction:
    """One extraction plus where it should go."""

    kind: DraftKind
    payload: Dict[str, Any]
    confidence: float
    routing: RoutingDecision
    extracted_by: str

    @property
    def is_auto_insert(self) -> bool:
        return self.routing is RoutingDecision.AUTO_INSERT

    @property
    def needs_review(self) -> bool:
        return self.routing in (RoutingDecision.DRAFT, RoutingDecision.MANUAL)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "payload": self.payload,
            "confidence": self.confidence,
            "routing": self.routing.value,
            "extracted_by": self.extracted_by,
        }


class ConfidenceRouter:
    """Scores extractions and assigns each a routing decision."""

    def __init__(
        self,
        auto_insert_threshold: Optional[float] = None,
        draft_threshold: Optional[float] = None,
        default_extracted_by: Optional[str] = None,
    ) -> None:
        self.auto_insert_threshold = (
            settings.CONFIDENCE_AUTO_INSERT if auto_insert_threshold is None else auto_insert_threshold
        )
        self.draft_threshold = (
            settings.CONFIDENCE_DRAFT if draft_threshold is None else draft_threshold
        )
        self.default_extracted_by = default_extracted_by or settings.EXTRACTION_MODEL_VERSION

        if self.draft_threshold > self.auto_insert_threshold:
            raise ValueError(
                "CONFIDENCE_DRAFT must not exceed CONFIDENCE_AUTO_INSERT "
                f"(got {self.draft_threshold} > {self.auto_insert_threshold})"
            )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, item: Dict[str, Any], *, agreed: bool = False) -> float:
        """Adjust a raw confidence by signals we can verify ourselves."""
        raw = item.get("confidence")
        try:
            confidence = float(raw)
        except (TypeError, ValueError):
            confidence = HEURISTIC_BASE_CONFIDENCE

        evidence = (item.get("evidence") or "").strip()
        if not evidence:
            # The ontology and the README both require evidence; without it
            # there is nothing for a curator to check, so it cannot be
            # promoted no matter what the model claimed.
            return 0.0
        if len(evidence) < MIN_SUBSTANTIAL_EVIDENCE_CHARS:
            confidence -= THIN_EVIDENCE_PENALTY
        if agreed:
            confidence += AGREEMENT_BONUS

        return max(0.0, min(1.0, confidence))

    def decide(self, confidence: float) -> RoutingDecision:
        if confidence >= self.auto_insert_threshold:
            return RoutingDecision.AUTO_INSERT
        if confidence >= self.draft_threshold:
            return RoutingDecision.DRAFT
        if confidence > 0:
            return RoutingDecision.MANUAL
        return RoutingDecision.DISCARDED

    def route_one(
        self, item: Dict[str, Any], kind: DraftKind, *, agreed: bool = False
    ) -> RoutedExtraction:
        confidence = self.score(item, agreed=agreed)
        return RoutedExtraction(
            kind=kind,
            payload=item,
            confidence=round(confidence, 4),
            routing=self.decide(confidence),
            extracted_by=item.get("extracted_by") or self.default_extracted_by,
        )

    def route(
        self,
        items: Iterable[Dict[str, Any]],
        kind: DraftKind,
        *,
        agreed_keys: Optional[set] = None,
    ) -> List[RoutedExtraction]:
        key_of = entity_key if kind is DraftKind.ENTITY else relation_key
        return [
            self.route_one(
                item, kind, agreed=bool(agreed_keys) and key_of(item) in agreed_keys
            )
            for item in items
        ]

    # ------------------------------------------------------------------
    # Merging two extractors' output
    # ------------------------------------------------------------------

    def merge_and_route(
        self,
        heuristic_entities: Sequence[Dict[str, Any]],
        llm_entities: Sequence[Dict[str, Any]],
        heuristic_relations: Sequence[Dict[str, Any]],
        llm_relations: Sequence[Dict[str, Any]],
    ) -> Tuple[List[RoutedExtraction], List[RoutedExtraction]]:
        """
        Combine the deterministic and LLM passes into one routed set.

        Items found by both are deduplicated *and* get the agreement bonus,
        which is the whole point of running two extractors: independent
        agreement is the strongest signal available without a human.
        """
        entities = _merge(
            heuristic_entities, llm_entities, entity_key, HEURISTIC_BASE_CONFIDENCE
        )
        relations = _merge(
            heuristic_relations, llm_relations, relation_key, HEURISTIC_BASE_CONFIDENCE
        )

        routed_entities = [
            self.route_one(item, DraftKind.ENTITY, agreed=agreed) for item, agreed in entities
        ]
        routed_relations = [
            self.route_one(item, DraftKind.RELATION, agreed=agreed) for item, agreed in relations
        ]

        logger.info(
            "extractions_routed",
            entities=len(routed_entities),
            relations=len(routed_relations),
            auto_insert=sum(
                1 for r in routed_entities + routed_relations if r.is_auto_insert
            ),
            queued=sum(1 for r in routed_entities + routed_relations if r.needs_review),
        )
        return routed_entities, routed_relations


# ======================================================================
# Identity helpers
# ======================================================================

def entity_key(entity: Dict[str, Any]) -> Tuple[str, str]:
    return ((entity.get("name") or "").strip().casefold(), (entity.get("type") or "").strip())


def relation_key(relation: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        (relation.get("source") or "").strip().casefold(),
        (relation.get("relation") or "").strip(),
        (relation.get("target") or "").strip().casefold(),
    )


def _merge(
    heuristic: Sequence[Dict[str, Any]],
    llm: Sequence[Dict[str, Any]],
    key_fn,
    heuristic_confidence: float,
) -> List[Tuple[Dict[str, Any], bool]]:
    """
    Union of both extractors' items, tagged with whether both found it.

    On agreement the LLM's version of the item wins, because it carries the
    richer fields (a model-reported confidence and a quoted evidence span,
    where the heuristic supplies a regex-window snippet).
    """
    by_key: Dict[Any, Dict[str, Any]] = {}
    heuristic_keys = set()

    for item in heuristic:
        key = key_fn(item)
        heuristic_keys.add(key)
        enriched = dict(item)
        enriched.setdefault("confidence", heuristic_confidence)
        enriched.setdefault("extracted_by", "heuristic-v1")
        by_key.setdefault(key, enriched)

    llm_keys = set()
    for item in llm:
        key = key_fn(item)
        llm_keys.add(key)
        by_key[key] = dict(item)  # LLM version wins on collision

    agreed = heuristic_keys & llm_keys
    return [(item, key in agreed) for key, item in by_key.items()]
