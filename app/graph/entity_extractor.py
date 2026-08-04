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

    # Types whose ``name`` is expected to be an actual proper name, and so
    # must survive the fragment/stopword checks in _is_plausible_name().
    # Claim and Experiment are excluded: a Claim's name is a statement
    # snippet, and an Experiment's is derived from its section heading.
    NAME_LIKE_TYPES = {"Method", "Dataset", "Task", "Metric"}

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

    # Function words that are never an entity on their own, and never a
    # sensible *start* to one. The fallback regexes below deliberately
    # anchor on capitalisation to pick out proper nouns ("BERT model"),
    # but a capitalised determiner at the start of a sentence ("The
    # model", "Our approach") satisfies that just as well -- which is
    # how articles and pronouns ended up stored as Methods/Datasets.
    STOPWORD_NAMES = {
        "a", "an", "the", "this", "that", "these", "those", "it", "its",
        "we", "our", "ours", "us", "they", "their", "theirs", "them",
        "he", "she", "his", "her", "i", "my", "you", "your",
        "all", "both", "each", "every", "any", "some", "such", "same",
        "other", "another", "one", "two", "three", "first", "second",
        "no", "not", "only", "also", "then", "than", "there", "here",
        "which", "who", "whom", "whose", "what", "when", "where", "how",
        "and", "or", "but", "if", "so", "as", "of", "in", "on", "for",
        "to", "by", "with", "from", "at", "is", "are", "was", "were",
        "be", "been", "being", "has", "have", "had", "do", "does", "did",
        "can", "could", "will", "would", "may", "might", "must", "should",
        "proposed", "existing", "previous", "recent", "new", "novel",
        "several", "many", "most", "more", "less", "very", "however",
    }

    # A trailing token from this set means the capture ran off the end of
    # a phrase and grabbed a dangling connective -- a fragment, not a name.
    DANGLING_TAIL_WORDS = {
        "a", "an", "the", "of", "in", "on", "for", "to", "by", "with",
        "from", "at", "and", "or", "but", "as", "that", "which", "is",
        "are", "was", "were", "be", "been", "than", "then", "into",
        "over", "under", "between", "through", "during", "per",
    }

    # Connectives that shouldn't appear *inside* a name -- their presence
    # means the capture swallowed a preposition and joined two separate
    # things ("BERT on the SQuAD"). "of" is deliberately allowed, since
    # it shows up in genuine names ("bag of words", "mixture of experts").
    INTERIOR_CONNECTORS = {
        "the", "a", "an", "on", "in", "at", "and", "or", "with",
        "from", "by", "to", "that", "which", "we", "our", "this",
    }

    # Verb/clause markers: their presence means the capture spans a
    # clause boundary, i.e. it's a sentence fragment rather than a name.
    _CLAUSE_MARKER_RE = re.compile(
        r"\b(?:is|are|was|were|be|been|being|has|have|had|does|do|did|"
        r"can|could|will|would|may|might|must|should|enables?|denotes?|"
        r"shows?|allows?|requires?|provides?|contains?|includes?|"
        r"consists?|employs?|leverages?|that|which|whose|whereas|while|"
        r"because|therefore|thus|hence)\b",
        re.IGNORECASE,
    )

    # Longest plausible multi-word entity name. Real method/dataset names
    # are short ("Multi-Head Attention", "CIFAR-100"); anything longer is
    # almost always a captured clause.
    MAX_NAME_WORDS = 6

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
                r"\b(?i:propose|introduce|present|use|using|develop)\s+(?i:a|an|the)?\s*([A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\s+(?i:model|method|architecture|framework|algorithm|approach)\b",
                r"\b([A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4})\s+(?i:model|method|architecture|framework|algorithm|approach)\b",
            ],
        ):
            self._add_entity(entities, seen, method, "Method", source_section, sentence)

        for dataset in self._known_terms(sentence, self.KNOWN_DATASETS):
            self._add_entity(entities, seen, dataset, "Dataset", source_section, sentence)

        for dataset in self._regex_names(
            sentence,
            [
                r"\b(?i:on|using|with|from)\s+([A-Z][A-Za-z0-9-]*(?:[- ][A-Za-z0-9]+){0,4})\s+(?i:dataset|corpus|benchmark)\b",
                r"\b([A-Z][A-Za-z0-9-]*(?:[- ][A-Za-z0-9]+){0,4})\s+(?i:dataset|corpus|benchmark)\b",
            ],
        ):
            self._add_entity(entities, seen, dataset, "Dataset", source_section, sentence)

        for task in self._known_terms(sentence, self.KNOWN_TASKS, ignore_case=True):
            self._add_entity(entities, seen, task, "Task", source_section, sentence)

        for task in self._regex_names(
            sentence,
            [
                r"\b(?i:for|on|solve|solves|address|addresses)\s+([a-z][a-z -]{3,40}?)\s+(?i:task|problem)\b",
                r"\b([a-z][a-z -]{3,40}?)\s+(?i:task|problem)\b",
            ],
        ):
            self._add_entity(entities, seen, task, "Task", source_section, sentence)

        for metric in self._known_terms(sentence, self.KNOWN_METRICS, ignore_case=True):
            self._add_entity(entities, seen, metric, "Metric", source_section, sentence)

        for metric in self._regex_names(
            sentence,
            [
                # Only accept a *metric-shaped* phrase -- one ending in an
                # explicit metric noun ("BLEU score", "error rate"). The
                # previous pattern captured any 1-3 words following
                # "achieves", which is how "the", "human" and "tight"
                # became Metrics.
                r"\b(?i:measured by|reports?|achieves?|obtains?|yields?)\s+(?i:a|an|the)?\s*"
                r"([A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9-]*){0,2}\s+(?i:score|rate|error))\b",
            ],
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

    # How the paper relates to an entity, inferred from the verb used in
    # the sentence that mentioned it. Lets PaperGraphBuilder emit the
    # specific ontology edge (INTRODUCES / USES_DATASET / SOLVES_TASK)
    # instead of a blanket MENTIONS for everything.
    _ROLE_PATTERNS = {
        "Method": [
            (r"\b(?i:propose[sd]?|introduc(?:e|es|ed|ing)|present[sd]?|develop(?:s|ed)?)\b", "introduces"),
            (r"\b(?i:use[sd]?|using|employ(?:s|ed)?|adopt(?:s|ed)?|appl(?:y|ies|ied))\b", "uses_method"),
        ],
        "Dataset": [
            (r"\b(?i:evaluat(?:e|es|ed)|train(?:s|ed)?|test(?:s|ed)?|benchmark(?:s|ed)?|use[sd]?|using)\b", "uses_dataset"),
        ],
        "Task": [
            (r"\b(?i:solve[sd]?|address(?:es|ed)?|target(?:s|ed)?|tackle[sd]?|for)\b", "solves_task"),
        ],
    }

    def _paper_role(self, sentence: str, entity_type: str) -> Optional[str]:
        for pattern, role in self._ROLE_PATTERNS.get(entity_type, []):
            if re.search(pattern, sentence):
                return role
        return None

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
        # Only the "name-like" types must look like names. A Claim is a
        # statement and an Experiment is derived from its section, so
        # holding those to the same rule would drop them entirely.
        if entity_type in self.NAME_LIKE_TYPES and not self._is_plausible_name(name):
            return

        key = (entity_type.lower(), name.lower())
        if key in seen:
            return

        seen.add(key)
        entity = {
            "name": name,
            "type": entity_type,
            "source_section": source_section,
            "evidence": evidence,
        }
        role = self._paper_role(evidence, entity_type)
        if role:
            entity["role"] = role
        entities.append(entity)

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
        """
        Run capture patterns *case-sensitively*.

        These patterns anchor on capitalisation (``[A-Z]``) to pick out
        proper nouns; matching them with ``re.IGNORECASE`` silently
        defeated that, so "the model" captured "the" and "The dataset"
        captured "The". Trigger words that genuinely need to match either
        case use an inline ``(?i:...)`` group instead.
        """
        names = []
        for pattern in patterns:
            for match in re.finditer(pattern, sentence):
                names.append(match.group(1))
        return names

    def _matches_any(self, sentence: str, patterns: Iterable[str]) -> bool:
        return any(re.search(pattern, sentence, re.IGNORECASE) for pattern in patterns)

    def _claim_name(self, sentence: str) -> str:
        """
        Short label for a claim node.

        A claim's "name" is necessarily a statement rather than a proper
        name, but hard-cutting at 12 words produced labels that ended
        mid-phrase and read like corrupt data. Mark the truncation so it
        's visibly a snippet; the full sentence is kept as evidence.
        """
        cleaned = self._clean_evidence(sentence)
        words = cleaned.split()
        if len(words) <= 12:
            return cleaned
        return " ".join(words[:12]) + "…"

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

    def _is_plausible_name(self, name: str) -> bool:
        """
        Reject captures that are function words or sentence fragments
        rather than entity names.

        The capture patterns are deliberately permissive so they can find
        names not in the known-term lists, which means they also pick up
        the occasional clause. This is the backstop that keeps things
        like "The", "All", "This enables the" and "denote each stream..."
        out of the graph.
        """
        words = name.split()
        if not words:
            return False

        lowered = [w.lower().strip(".,;:()[]{}") for w in words]

        # Entirely function words ("The", "All", "Our", "of the").
        if all(w in self.STOPWORD_NAMES for w in lowered):
            return False

        # Starts with a determiner/pronoun -- "This enables the", "Our".
        if lowered[0] in self.STOPWORD_NAMES:
            return False

        # Dangles on a connective -- "the majority of", "compared to".
        if lowered[-1] in self.DANGLING_TAIL_WORDS:
            return False

        # Joins two things across a preposition -- "BERT on the SQuAD".
        if any(w in self.INTERIOR_CONNECTORS for w in lowered[1:-1]):
            return False

        # Spans a clause boundary: it's a sentence, not a name.
        if self._CLAUSE_MARKER_RE.search(name):
            return False

        if len(words) > self.MAX_NAME_WORDS:
            return False

        # Needs at least one letter -- pure punctuation/digits aren't names.
        if not re.search(r"[A-Za-z]", name):
            return False

        return True

    def _clean_evidence(self, value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()
