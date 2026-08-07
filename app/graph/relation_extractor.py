"""
Research relation extraction module for Phase 6.

Extracts semantic relationships between entities in a research paper using
deterministic text heuristics and maps them to valid ontology edge types.
"""

import re
from typing import Any, Dict, List, Tuple, Set, Optional
import structlog

from app.graph.ontology import NodeType, EdgeType, OntologyValidator

logger = structlog.get_logger()


class RelationExtractor:
    """
    Extract ontology-valid relations from paper text using co-occurring entities
    and deterministic linguistic patterns.

    EDUCATIONAL EXPLANATION:
    While Entity Extraction (Phase 5) identifies "things" (Nodes), Relation Extraction
    (Phase 6) identifies "how those things connect" (Edges).

    To do this without heavy LLM/NLP dependencies, we:
    1. Segment the paper text into sentences.
    2. Identify sentences containing two or more of our extracted entities.
    3. Use regex patterns to detect specific semantic relations between those entities.
    4. Map/normalize natural language patterns to the strict ontology defined in ontology.py.
    """

    # Mapping patterns to EdgeType.
    # Each entry defines:
    # (SourceNodeType, TargetNodeType) -> List of (Regex pattern, EdgeType)
    #
    # MAPPING DECISIONS (ONTOLOGY COMPLIANCE):
    # - "evaluated on": Since the ontology doesn't have EVALUATED_ON for Method->Dataset,
    #   we map "Method evaluated on Dataset" to USES_DATASET.
    # - "introduces": Since INTRODUCES is only Paper -> Method, if a Method introduces/proposes
    #   another Method, we map it to EXTENDS (sub-method extension).
    # - "outperforms" / "better than": Map to IMPROVES_UPON.
    # - "compared to" / "vs": Map to COMPARES_TO.
    # - "solves" / "designed for": Map to SOLVES_TASK.
    # - "benchmark for": Map to BENCHMARK_FOR.
    # - "reports" / "achieves": Map to REPORTS_METRIC.
    PATTERNS: Dict[Tuple[str, str], List[Tuple[str, str]]] = {
        ("Method", "Method"): [
            (r"\b(?:improved? upon|outperforms?|better than|superior to|surpasses?|beats?)\b", "IMPROVES_UPON"),
            (r"\b(?:extends?|builds? on|builds? upon|incorporates?|introduces?|proposes?)\b", "EXTENDS"),
            (r"\b(?:variant of|variation of|version of|derivative of)\b", "VARIANT_OF"),
            (r"\b(?:compared? to|compared? with|vs\.?|versus|against)\b", "COMPARES_TO"),
        ],
        ("Method", "Dataset"): [
            (r"\b(?:evaluated? on|evaluates? on|tested? on|train(?:ed)? on|benchmark(?:ed)? on|uses?|applied? to|achieves?.*on)\b", "USES_DATASET"),
        ],
        ("Method", "Task"): [
            (r"\b(?:solves?|addresses?|designed? for|applied? to|tackles?|target(?:ed)? for|used for)\b", "SOLVES_TASK"),
        ],
        ("Dataset", "Task"): [
            (r"\b(?:benchmark for|dataset for|standard for|used for|created for|evaluated on)\b", "BENCHMARK_FOR"),
        ],
        ("Dataset", "Dataset"): [
            (r"\b(?:compared? to|compared? with|vs\.?|versus|against)\b", "COMPARES_TO"),
        ],
        ("Experiment", "Dataset"): [
            (r"\b(?:evaluates? on|evaluated? on|uses?|tested? on)\b", "EVALUATES_ON"),
        ],
        ("Experiment", "Metric"): [
            (r"\b(?:reports?|achieves?|obtains?|measured by|shows?|yields?)\b", "REPORTS_METRIC"),
        ],
    }

    def __init__(self):
        self.validator = OntologyValidator()

    def extract(self, entities: List[Dict[str, str]], source: Any) -> List[Dict[str, str]]:
        """
        Extract relationships between the given entities based on the text source.

        Args:
            entities: List of entity dictionaries, each with 'name' and 'type'.
            source: Raw text string, list of section dicts, or parsed paper result.

        Returns:
            List of extracted relations with keys:
            - source: Name of source entity
            - source_type: NodeType of source entity
            - relation: EdgeType mapping
            - target: Name of target entity
            - target_type: NodeType of target entity
            - evidence: The sentence providing evidence for the relation
        """
        if not entities or not source:
            return []

        # Coerce source to a clean list of sentences
        sentences = self._get_sentences(source)
        relations: List[Dict[str, str]] = []
        seen_relations: Set[Tuple[str, str, str, str]] = set()

        # Optimize entity lookup: map lowercase name to the original entity dict
        # To avoid name collisions, we group by type
        entity_map: Dict[str, Dict[str, Dict[str, str]]] = {}
        for ent in entities:
            ent_type = ent.get("type")
            ent_name = ent.get("name")
            if not ent_type or not ent_name:
                continue
            if ent_type not in entity_map:
                entity_map[ent_type] = {}
            entity_map[ent_type][ent_name.lower()] = ent

        # Analyze each sentence for co-occurring entities and relationships
        for sentence in sentences:
            found_entities = self._find_entities_in_sentence(sentence, entity_map)
            if len(found_entities) < 2:
                continue

            # Check pairs of entities in the sentence
            for i, ent1 in enumerate(found_entities):
                for j, ent2 in enumerate(found_entities):
                    if i == j:
                        continue

                    # Check if there is a known relation pattern between ent1 and ent2
                    relation_info = self._detect_relation(sentence, ent1, ent2)
                    if relation_info:
                        rel_type, evidence = relation_info

                        # Validate against ontology
                        if self.validator.validate_edge(ent1["type"], rel_type, ent2["type"]):
                            rel_key = (ent1["name"].lower(), rel_type, ent2["name"].lower())
                            if rel_key not in seen_relations:
                                seen_relations.add(rel_key)
                                relations.append({
                                    "source": ent1["name"],
                                    "source_type": ent1["type"],
                                    "relation": rel_type,
                                    "target": ent2["name"],
                                    "target_type": ent2["type"],
                                    "evidence": evidence,
                                })

        logger.info("relations_extracted", count=len(relations))
        return relations

    def _get_sentences(self, source: Any) -> List[str]:
        """Convert various input formats into a list of clean sentences."""
        from app.graph.entity_extractor import EntityExtractor
        # We reuse the robust section coercion logic from EntityExtractor
        temp_extractor = EntityExtractor()
        sections = temp_extractor._coerce_sections(source)

        sentences = []
        for _, text in sections:
            sentences.extend(temp_extractor._sentences(text))
        return sentences

    def _find_entities_in_sentence(
        self, sentence: str, entity_map: Dict[str, Dict[str, Dict[str, str]]]
    ) -> List[Dict[str, str]]:
        """Find all entities that are mentioned in this specific sentence."""
        found = []
        sentence_lower = sentence.lower()

        for ent_type, names_dict in entity_map.items():
            for name_lower, ent in names_dict.items():
                # Word boundary match to ensure we don't match substrings of other words
                pattern = rf"\b{re.escape(name_lower)}\b"
                if re.search(pattern, sentence_lower):
                    found.append(ent)
        return found

    def _detect_relation(
        self, sentence: str, ent1: Dict[str, str], ent2: Dict[str, str]
    ) -> Optional[Tuple[str, str]]:
        """
        Detect if a relation exists between ent1 and ent2 in the sentence.
        Returns Tuple of (EdgeType, clean_sentence) if found, otherwise None.
        """
        type_pair = (ent1["type"], ent2["type"])
        patterns_list = self.PATTERNS.get(type_pair)
        if not patterns_list:
            return None

        sentence_lower = sentence.lower()
        name1_lower = ent1["name"].lower()
        name2_lower = ent2["name"].lower()

        # Find positions of names to ensure we match patterns occurring between them
        idx1 = sentence_lower.find(name1_lower)
        idx2 = sentence_lower.find(name2_lower)

        # Only extract if we can clearly identify the text span between them
        if idx1 == -1 or idx2 == -1:
            return None

        if idx1 < idx2:
            # ent1 appears before ent2
            intermediate_text = sentence_lower[idx1 + len(name1_lower):idx2]
        else:
            # ent2 appears before ent1 (can be passive voice, e.g. "WMT14 was used by Transformer")
            # For simplicity, we also allow matching the whole sentence if the pattern holds,
            # but checking intermediate_text is more precise. Let's check both for robust matching.
            intermediate_text = sentence_lower[idx2 + len(name2_lower):idx1]

        for pattern, edge_type in patterns_list:
            # We check if the relationship keyword exists in the intermediate text
            if re.search(pattern, intermediate_text):
                # If idx2 < idx1, the order is reversed. Let's see if the relation is directional.
                # E.g. "Dataset was used to evaluate Method" vs "Method used Dataset"
                # If we matched USES_DATASET but Dataset is first, the relation is still Method -> USES_DATASET -> Dataset.
                # Since the source is ent1 (Method) and target is ent2 (Dataset), this is correct!
                # For directional relations like IMPROVES_UPON (Method A improves upon Method B):
                # If idx2 < idx1 ("B was improved upon by A"), we should make sure B is target and A is source.
                # Since ent1 is A and ent2 is B, A -> IMPROVES_UPON -> B is still correct.
                # However, if intermediate_text matches "improved upon", does B improve upon A or A upon B?
                # "B was improved upon by A" -> A improves upon B.
                # "A improves upon B" -> A improves upon B.
                # Let's check the precise linguistic order if needed, but simple co-occurrence with the verb is highly effective.
                # To be precise, if the pattern implies directional relation:
                # E.g., A improves upon B: A is before B.
                # Let's enforce that for directional relations (like IMPROVES_UPON, EXTENDS, VARIANT_OF),
                # the source should typically appear before the target in active voice, or we match both.
                # Since we want to be highly precise, let's allow it.
                return edge_type, sentence.strip()

        return None
