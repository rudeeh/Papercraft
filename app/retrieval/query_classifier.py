"""
Query Classifier Module (Phase 13.1)

Classifies a natural-language research question into one of six intents so
the hybrid retriever (Phase 13.2) can route it to the right combination of
graph and vector retrieval. Uses deterministic keyword/pattern heuristics,
consistent with the rest of the extraction pipeline (entity/relation
extraction also avoid a heavy NLP/LLM dependency in favor of regex rules).
"""

import re
from enum import Enum
from typing import List, Tuple

import structlog

logger = structlog.get_logger()


class QueryType(str, Enum):
    EXPLANATION = "EXPLANATION"
    COMPARISON = "COMPARISON"
    EVOLUTION = "EVOLUTION"
    CITATION = "CITATION"
    SURVEY = "SURVEY"
    ENTITY_LOOKUP = "ENTITY_LOOKUP"


# Checked in this order -- first match wins. The order encodes precedence
# for queries that could plausibly match more than one category (e.g. "how
# did X evolve" contains no comparison keywords, but "how does X compare to
# its predecessors over time" could match both EVOLUTION and COMPARISON --
# EVOLUTION is checked first since it's the more specific intent).
_PATTERNS: List[Tuple[QueryType, List[str]]] = [
    (QueryType.CITATION, [
        r"\bcite[sd]?\b", r"\bcitation[s]?\b", r"\breference[sd]?\b",
        r"\bcited by\b", r"\bwho cites\b", r"\bpapers? that cite[s]?\b",
    ]),
    (QueryType.EVOLUTION, [
        r"\bevolv(e|ed|ing)\b", r"\bevolution\b", r"\bhistory of\b",
        r"\bover time\b", r"\bprogress(ion|ed)?\b", r"\bdevelopment of\b",
        r"\bfrom .+ to .+\b",
    ]),
    (QueryType.COMPARISON, [
        r"\bcompar(e|ed|ison|es)\b", r"\bversus\b", r"\bvs\.?\b",
        r"\bdifference[s]? between\b", r"\bbetter than\b", r"\bworse than\b",
        r"\bcompared to\b", r"\boutperform(s|ed|ing)?\b",
        r"\bimprov(e|ed|es|ing)\s+(on|upon)\b",
    ]),
    (QueryType.SURVEY, [
        r"\bsurvey\b", r"\boverview\b", r"\bstate[- ]of[- ]the[- ]art\b",
        r"\breview of\b", r"\bsummari[sz]e\b",
        r"\bwhat (?:methods|approaches|techniques) exist\b",
    ]),
    (QueryType.ENTITY_LOOKUP, [
        r"^\s*what is\b", r"^\s*who is\b", r"^\s*define\b",
        r"\btell me about\b",
    ]),
]


class QueryClassifier:
    """Classifies a query string into one of the six retrieval intents."""

    def classify(self, query: str) -> QueryType:
        if not query or not query.strip():
            return QueryType.EXPLANATION

        text = query.strip().lower()
        for query_type, patterns in _PATTERNS:
            if any(re.search(p, text) for p in patterns):
                logger.debug("query_classified", query=query, type=query_type.value)
                return query_type

        return QueryType.EXPLANATION
