"""
Graph Ontology Module

Defines the strict schema for research-paper knowledge graphs:
- Allowed node types
- Allowed edge types  
- Validation functions
- Constraints and relationships

This ensures consistency across entity extraction, relation extraction,
and graph storage in Neo4j.
"""

from enum import Enum
from typing import Set, List, Tuple
import structlog

logger = structlog.get_logger()


class NodeType(str, Enum):
    """
    Allowed node types in research-paper graphs.
    
    LEARNING POINT: Enums
    We use Enum (Enumeration) for node types to ensure that we only use a predefined
    set of names. This prevents typos (like "paper" vs "Paper") from breaking our graph.

    Each node represents a distinct concept or entity in the research domain.
    """

    PAPER = "Paper"
    METHOD = "Method"
    DATASET = "Dataset"
    TASK = "Task"
    METRIC = "Metric"
    AUTHOR = "Author"
    INSTITUTION = "Institution"
    CLAIM = "Claim"
    EXPERIMENT = "Experiment"
    SECTION = "Section"


class EdgeType(str, Enum):
    """
    Allowed relationship types between nodes.
    
    Each edge represents a specific relationship between concepts.
    """

    # Citation relationships
    CITES = "CITES"  # Paper -> Paper
    CITED_BY = "CITED_BY"  # Paper -> Paper

    # Methodological relationships
    INTRODUCES = "INTRODUCES"  # Paper -> Method
    USES_METHOD = "USES_METHOD"  # Paper -> Method
    IMPROVES_UPON = "IMPROVES_UPON"  # Method -> Method
    EXTENDS = "EXTENDS"  # Method -> Method
    VARIANT_OF = "VARIANT_OF"  # Method -> Method

    # Data relationships
    USES_DATASET = "USES_DATASET"  # Paper/Experiment -> Dataset
    PUBLISHED_DATASET = "PUBLISHED_DATASET"  # Paper -> Dataset
    EVALUATES_ON = "EVALUATES_ON"  # Experiment -> Dataset
    BENCHMARK_FOR = "BENCHMARK_FOR"  # Dataset -> Task

    # Task relationships
    SOLVES_TASK = "SOLVES_TASK"  # Paper/Method -> Task
    RELATED_TASK = "RELATED_TASK"  # Task -> Task

    # Metric relationships
    REPORTS_METRIC = "REPORTS_METRIC"  # Experiment -> Metric
    MEASURED_BY = "MEASURED_BY"  # Result -> Metric

    # Authorship relationships
    WRITTEN_BY = "WRITTEN_BY"  # Paper -> Author
    AUTHORED_BY = "AUTHORED_BY"  # Paper -> Author

    # Institutional relationships
    AFFILIATED_WITH = "AFFILIATED_WITH"  # Author -> Institution

    # Structural relationships
    HAS_SECTION = "HAS_SECTION"  # Paper -> Section
    CONTAINS_CLAIM = "CONTAINS_CLAIM"  # Section -> Claim

    # Cross-paper relationships
    MENTIONS = "MENTIONS"  # Paper -> Entity (generic mention)
    COMPARES_TO = "COMPARES_TO"  # Method -> Method or Dataset -> Dataset


# Valid combinations: (SourceNodeType, EdgeType) -> Set[TargetNodeType]
# LEARNING POINT: Mapping Relationships
# This dictionary defines the "rules" of our graph. For example, it says that
# a PAPER can CITE another PAPER, but it cannot CITE a DATASET.
VALID_EDGES = {
    # Paper relationships
    (NodeType.PAPER, EdgeType.CITES): {NodeType.PAPER},
    (NodeType.PAPER, EdgeType.CITED_BY): {NodeType.PAPER},
    (NodeType.PAPER, EdgeType.INTRODUCES): {NodeType.METHOD},
    (NodeType.PAPER, EdgeType.USES_METHOD): {NodeType.METHOD},
    (NodeType.PAPER, EdgeType.USES_DATASET): {NodeType.DATASET},
    (NodeType.PAPER, EdgeType.PUBLISHED_DATASET): {NodeType.DATASET},
    (NodeType.PAPER, EdgeType.SOLVES_TASK): {NodeType.TASK},
    (NodeType.PAPER, EdgeType.WRITTEN_BY): {NodeType.AUTHOR},
    (NodeType.PAPER, EdgeType.HAS_SECTION): {NodeType.SECTION},
    (NodeType.PAPER, EdgeType.MENTIONS): {
        NodeType.METHOD,
        NodeType.DATASET,
        NodeType.TASK,
        NodeType.METRIC,
    },
    # Method relationships
    (NodeType.METHOD, EdgeType.IMPROVES_UPON): {NodeType.METHOD},
    (NodeType.METHOD, EdgeType.EXTENDS): {NodeType.METHOD},
    (NodeType.METHOD, EdgeType.VARIANT_OF): {NodeType.METHOD},
    (NodeType.METHOD, EdgeType.SOLVES_TASK): {NodeType.TASK},
    (NodeType.METHOD, EdgeType.USES_DATASET): {NodeType.DATASET},
    (NodeType.METHOD, EdgeType.COMPARES_TO): {NodeType.METHOD},
    # Dataset relationships
    (NodeType.DATASET, EdgeType.BENCHMARK_FOR): {NodeType.TASK},
    (NodeType.DATASET, EdgeType.COMPARES_TO): {NodeType.DATASET},
    # Task relationships
    (NodeType.TASK, EdgeType.RELATED_TASK): {NodeType.TASK},
    # Experiment relationships
    (NodeType.EXPERIMENT, EdgeType.EVALUATES_ON): {NodeType.DATASET},
    (NodeType.EXPERIMENT, EdgeType.REPORTS_METRIC): {NodeType.METRIC},
    # Author relationships
    (NodeType.AUTHOR, EdgeType.AFFILIATED_WITH): {NodeType.INSTITUTION},
    # Section relationships
    (NodeType.SECTION, EdgeType.CONTAINS_CLAIM): {NodeType.CLAIM},
}


class OntologyValidator:
    """Validates nodes and edges against the ontology."""

    @staticmethod
    def validate_node_type(node_type: str) -> bool:
        """Check if node type is valid."""
        try:
            NodeType(node_type)
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_edge_type(edge_type: str) -> bool:
        """Check if edge type is valid."""
        try:
            EdgeType(edge_type)
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_edge(source_type: str, edge_type: str, target_type: str) -> bool:
        """
        Validate if an edge is allowed between source and target node types.

        Args:
            source_type: NodeType string
            edge_type: EdgeType string
            target_type: NodeType string

        Returns:
            True if edge is valid, False otherwise
        """
        try:
            source = NodeType(source_type)
            edge = EdgeType(edge_type)
            target = NodeType(target_type)
        except ValueError:
            return False

        key = (source, edge)
        return key in VALID_EDGES and target in VALID_EDGES[key]

    @staticmethod
    def get_allowed_targets(source_type: str, edge_type: str) -> Set[str]:
        """Get set of allowed target node types for a given source and edge."""
        try:
            source = NodeType(source_type)
            edge = EdgeType(edge_type)
            key = (source, edge)
            if key in VALID_EDGES:
                return {t.value for t in VALID_EDGES[key]}
        except ValueError:
            pass
        return set()


class Node:
    """Represents a node in the knowledge graph."""

    def __init__(
        self,
        node_id: str,
        node_type: str,
        name: str,
        paper_id: str,
        properties: dict = None,
    ):
        """
        Initialize a node.

        Args:
            node_id: Unique identifier (usually paper_id + entity hash)
            node_type: NodeType enum value
            name: Human-readable name
            paper_id: ID of paper this node originated from
            properties: Additional metadata
        """
        if not OntologyValidator.validate_node_type(node_type):
            raise ValueError(f"Invalid node type: {node_type}")

        self.node_id = node_id
        self.node_type = node_type
        self.name = name
        self.paper_id = paper_id
        self.properties = properties or {}

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "name": self.name,
            "paper_id": self.paper_id,
            "properties": self.properties,
        }


class Edge:
    """Represents an edge (relationship) in the knowledge graph."""

    def __init__(
        self,
        source_id: str,
        source_type: str,
        edge_type: str,
        target_id: str,
        target_type: str,
        evidence: str = None,
        confidence: float = 1.0,
        properties: dict = None,
    ):
        """
        Initialize an edge.

        Args:
            source_id: Source node ID
            source_type: Source node type
            edge_type: EdgeType enum value
            target_id: Target node ID
            target_type: Target node type
            evidence: Text snippet supporting this relationship
            confidence: Confidence score (0.0 to 1.0)
            properties: Additional metadata
        """
        if not OntologyValidator.validate_edge(source_type, edge_type, target_type):
            raise ValueError(
                f"Invalid edge: {source_type} --{edge_type}--> {target_type}"
            )

        self.source_id = source_id
        self.source_type = source_type
        self.edge_type = edge_type
        self.target_id = target_id
        self.target_type = target_type
        self.evidence = evidence
        self.confidence = max(0.0, min(1.0, confidence))  # Clamp to [0, 1]
        self.properties = properties or {}

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "edge_type": self.edge_type,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "properties": self.properties,
        }


def get_all_node_types() -> List[str]:
    """Get list of all valid node types."""
    return [t.value for t in NodeType]


def get_all_edge_types() -> List[str]:
    """Get list of all valid edge types."""
    return [t.value for t in EdgeType]


def describe_ontology() -> dict:
    """Return a description of the complete ontology."""
    return {
        "node_types": get_all_node_types(),
        "edge_types": get_all_edge_types(),
        "valid_edges": [
            {
                "source": k[0].value,
                "edge": k[1].value,
                "targets": [t.value for t in v],
            }
            for k, v in VALID_EDGES.items()
        ],
    }
