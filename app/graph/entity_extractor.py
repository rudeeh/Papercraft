"""
Research entity extraction for Phase 5.

The extractor uses deterministic text heuristics so the graph pipeline can
start producing structured entities without requiring a heavy NLP dependency.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import structlog

from app.graph.ontology import OntologyValidator

logger = structlog.get_logger()


class EntityExtractor:
    """Extract graph-ready research entities from parsed paper text."""

    ENTITY_TYPES = {"Method", "Dataset", "Task", "Metric", "Claim", "Experiment"}

    KNOWN_METHODS = {
        "Transformer",
        "BERT",
        "GPT",
        "GraphRAG",
        "RAG",
        "ResNet",
        "LSTM",
        "CNN",
        "SVM",
        "Random Forest",
        "Neural Network",
        "Self-Attention",
        "Multi-Head Attention",
    }

    KNOWN_DATASETS = {
        "ImageNet",
        "CIFAR-10",
        "CIFAR-100",
        "MNIST",
        "COCO",
        "SQuAD",
        "GLUE",
        "SuperGLUE",
        "WMT14",
        "PubMed",
    }

    KNOWN_TASKS = {
        "machine translation",
        "question answering",
        "image classification",
        "object detection",
        "semantic search",
        "document retrieval",
        "named entity recognition",
        "text classification",
        "summarization",
        "information extraction",
    }

    KNOWN_METRICS = {
        "accuracy",
        "precision",
        "recall",
        "F1",
        "F1-score",
        "BLEU",
        "ROUGE",
        "AUC",
        "perplexity",
        "latency",
        "throughput",
        "MRR",
        "NDCG",
    }

    CLAIM_PATTERNS = [
        r"\bwe\s+(show|demonstrate|prove|find|observe|claim|conclude)\b",
        r"\bresults?\s+(show|demonstrate|indicate|suggest)\b",
        r"\b(outperform|improve|achieve|reduce|increase)s?\b",
        r"\bstate[- ]of[- ]the[- ]art\b",
    ]

    EXPERIMENT_PATTERNS = [
        r"\bexperiment(s|al)?\b",
        r"\bevaluat(e|ed|ion|ing)\b",
        r"\bablation\b",
        r"\bbenchmark\b",
        r"\bbaseline(s)?\b",
    ]

    def extract(self, source: Any) -> List[Dict[str, str]]:
        """
        Return entities as JSON-serializable dictionaries.

        Supported inputs:
        - raw text string
        - list of parser-style section dictionaries
        - parser result object or dict with ``abstract`` and ``sections``
        """
        sections = self._coerce_sections(source)
        entities: List[Dict[str, str]] = []
        seen = set()

        for section_name, text in sections:
            for sentence in self._sentences(text):
                self._extract_from_sentence(sentence, section_name, entities, seen)

        logger.info("entities_extracted", count=len(entities))
        return entities

    def _extract_from_sentence(
        self,
        sentence: str,
        source_section: str,
        entities: List[Dict[str, str]],
        seen: set,
    ) -> None:
        for method in self._known_terms(sentence, self.KNOWN_METHODS):
            self._add_entity(entities, seen, method, "Method", source_section, sentence)

        for method in self._regex_names(
            sentence,
            [
                r"\b(?:propose|introduce|present|use|using|develop)\s+(?:a|an|the)?\s*([A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\s+(?:model|method|architecture|framework|algorithm|approach)\b",
                r"\b([A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\s+(?:model|method|architecture|framework|algorithm|approach)\b",
            ],
        ):
            self._add_entity(entities, seen, method, "Method", source_section, sentence)

        for dataset in self._known_terms(sentence, self.KNOWN_DATASETS):
            self._add_entity(entities, seen, dataset, "Dataset", source_section, sentence)

        for dataset in self._regex_names(
            sentence,
            [
                r"\b(?:on|using|with|from)\s+([A-Z][A-Za-z0-9-]*(?:[- ][A-Za-z0-9]+){0,4})\s+(?:dataset|corpus|benchmark)\b",
                r"\b([A-Z][A-Za-z0-9-]*(?:[- ][A-Za-z0-9]+){0,4})\s+(?:dataset|corpus|benchmark)\b",
            ],
        ):
            self._add_entity(entities, seen, dataset, "Dataset", source_section, sentence)

        for task in self._known_terms(sentence, self.KNOWN_TASKS, ignore_case=True):
            self._add_entity(entities, seen, task, "Task", source_section, sentence)

        for task in self._regex_names(
            sentence,
            [
                r"\b(?:for|on|solve|solves|address|addresses)\s+([a-z][a-z -]{3,60}?)\s+(?:task|problem)\b",
                r"\b([a-z][a-z -]{3,60}?)\s+(?:task|problem)\b",
            ],
        ):
            self._add_entity(entities, seen, task, "Task", source_section, sentence)

        for metric in self._known_terms(sentence, self.KNOWN_METRICS, ignore_case=True):
            self._add_entity(entities, seen, metric, "Metric", source_section, sentence)

        for metric in self._regex_names(
            sentence,
            [r"\b(?:measured by|reports?|achieves?)\s+([A-Z][A-Za-z0-9-]*|[a-z]+(?:\s+[a-z]+){0,2})\b"],
        ):
            self._add_entity(entities, seen, metric, "Metric", source_section, sentence)

        if self._matches_any(sentence, self.CLAIM_PATTERNS):
            self._add_entity(entities, seen, self._claim_name(sentence), "Claim", source_section, sentence)

        if self._matches_any(sentence, self.EXPERIMENT_PATTERNS):
            self._add_entity(
                entities,
                seen,
                self._experiment_name(sentence, source_section),
                "Experiment",
                source_section,
                sentence,
            )

    def _add_entity(
        self,
        entities: List[Dict[str, str]],
        seen: set,
        name: Optional[str],
        entity_type: str,
        source_section: str,
        evidence: str,
    ) -> None:
        name = self._clean_name(name)
        evidence = self._clean_evidence(evidence)
        source_section = self._clean_name(source_section) or "Unknown"

        if not name or not evidence:
            return
        if entity_type not in self.ENTITY_TYPES:
            return
        if not OntologyValidator.validate_node_type(entity_type):
            return

        key = (entity_type.lower(), name.lower())
        if key in seen:
            return

        seen.add(key)
        entities.append(
            {
                "name": name,
                "type": entity_type,
                "source_section": source_section,
                "evidence": evidence,
            }
        )

    def _coerce_sections(self, source: Any) -> List[Tuple[str, str]]:
        if source is None:
            return []

        if isinstance(source, str):
            return [("Unknown", source)]

        if isinstance(source, dict):
            sections = self._sections_from_mapping(source)
            return sections

        if isinstance(source, Sequence) and not isinstance(source, (bytes, bytearray)):
            return self._sections_from_sequence(source)

        if hasattr(source, "to_dict"):
            return self._sections_from_mapping(source.to_dict())

        abstract = getattr(source, "abstract", None)
        raw_sections = getattr(source, "sections", None)
        return self._sections_from_mapping({"abstract": abstract, "sections": raw_sections})

    def _sections_from_mapping(self, source: Dict[str, Any]) -> List[Tuple[str, str]]:
        sections: List[Tuple[str, str]] = []

        abstract = source.get("abstract")
        if isinstance(abstract, str) and abstract.strip():
            sections.append(("Abstract", abstract))

        sections.extend(self._sections_from_sequence(source.get("sections") or []))

        text = source.get("text")
        if not sections and isinstance(text, str) and text.strip():
            sections.append(("Unknown", text))

        return sections

    def _sections_from_sequence(self, raw_sections: Iterable[Any]) -> List[Tuple[str, str]]:
        sections: List[Tuple[str, str]] = []
        for item in raw_sections:
            if isinstance(item, dict):
                heading = item.get("heading") or item.get("source_section") or "Unknown"
                text = item.get("text") or item.get("content") or ""
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                heading, text = item[0], item[1]
            elif isinstance(item, str):
                heading, text = "Unknown", item
            else:
                continue

            if isinstance(text, str) and text.strip():
                sections.append((str(heading or "Unknown"), text))

        return sections

    def _sentences(self, text: str) -> List[str]:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return []
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]

    def _known_terms(
        self,
        sentence: str,
        terms: Iterable[str],
        ignore_case: bool = False,
    ) -> List[str]:
        found = []
        flags = re.IGNORECASE if ignore_case else 0
        for term in sorted(terms, key=len, reverse=True):
            if re.search(rf"\b{re.escape(term)}\b", sentence, flags):
                found.append(term)
        return found

    def _regex_names(self, sentence: str, patterns: Iterable[str]) -> List[str]:
        names = []
        for pattern in patterns:
            for match in re.finditer(pattern, sentence, re.IGNORECASE):
                names.append(match.group(1))
        return names

    def _matches_any(self, sentence: str, patterns: Iterable[str]) -> bool:
        return any(re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns)

    def _claim_name(self, sentence: str) -> str:
        words = self._clean_evidence(sentence).split()
        return " ".join(words[:12])

    def _experiment_name(self, sentence: str, source_section: str) -> str:
        section = self._clean_name(source_section) or "Experiment"
        if re.search(r"\bablation\b", sentence, re.IGNORECASE):
            return f"{section} ablation"
        if re.search(r"\bbenchmark\b", sentence, re.IGNORECASE):
            return f"{section} benchmark"
        return f"{section} experiment"

    def _clean_name(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None

        cleaned = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n,;:.()[]{}")
        cleaned = re.sub(r"^(a|an|the)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+(model|method|architecture|framework|algorithm|approach)$", "", cleaned, flags=re.IGNORECASE)

        if len(cleaned) < 2 or len(cleaned) > 120:
            return None
        if cleaned.lower() in {"method", "dataset", "task", "metric", "claim", "experiment"}:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
            return None

        return cleaned

    def _clean_evidence(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()
