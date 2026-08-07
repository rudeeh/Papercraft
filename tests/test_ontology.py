"""
Unit tests for graph ontology module.

Tests validate:
- Node type validation
- Edge type validation
- Edge relationship constraints
- Node and Edge serialization
"""

import pytest
from app.graph.ontology import (
    NodeType,
    EdgeType,
    OntologyValidator,
    Node,
    Edge,
    get_all_node_types,
    get_all_edge_types,
    describe_ontology,
)


class TestNodeTypeEnum:
    """Test NodeType enumeration."""

    def test_all_node_types_exist(self):
        """Verify all expected node types are defined."""
        expected = {
            "Paper",
            "Method",
            "Dataset",
            "Task",
            "Metric",
            "Author",
            "Institution",
            "Claim",
            "Experiment",
            "Section",
        }
        actual = {t.value for t in NodeType}
        assert actual == expected

    def test_node_type_access(self):
        """Test accessing node types by enum."""
        assert NodeType.PAPER.value == "Paper"
        assert NodeType.METHOD.value == "Method"
        assert NodeType.DATASET.value == "Dataset"


class TestEdgeTypeEnum:
    """Test EdgeType enumeration."""

    def test_all_edge_types_exist(self):
        """Verify core edge types are defined."""
        expected_edges = {
            "CITES",
            "INTRODUCES",
            "USES_DATASET",
            "SOLVES_TASK",
            "IMPROVES_UPON",
            "WRITTEN_BY",
            "HAS_SECTION",
            "CONTAINS_CLAIM",
        }
        actual = {t.value for t in EdgeType}
        assert expected_edges.issubset(actual)

    def test_edge_type_access(self):
        """Test accessing edge types by enum."""
        assert EdgeType.CITES.value == "CITES"
        assert EdgeType.USES_DATASET.value == "USES_DATASET"


class TestOntologyValidator:
    """Test OntologyValidator class."""

    def test_validate_valid_node_type(self):
        """Test validation of valid node types."""
        assert OntologyValidator.validate_node_type("Paper") == True
        assert OntologyValidator.validate_node_type("Method") == True
        assert OntologyValidator.validate_node_type("Dataset") == True

    def test_validate_invalid_node_type(self):
        """Test validation of invalid node types."""
        assert OntologyValidator.validate_node_type("InvalidType") == False
        assert OntologyValidator.validate_node_type("") == False
        assert OntologyValidator.validate_node_type("paper") == False  # case-sensitive

    def test_validate_valid_edge_type(self):
        """Test validation of valid edge types."""
        assert OntologyValidator.validate_edge_type("CITES") == True
        assert OntologyValidator.validate_edge_type("USES_DATASET") == True

    def test_validate_invalid_edge_type(self):
        """Test validation of invalid edge types."""
        assert OntologyValidator.validate_edge_type("INVALID_EDGE") == False
        assert OntologyValidator.validate_edge_type("cites") == False  # case-sensitive

    def test_validate_valid_edges(self):
        """Test validation of valid edge relationships."""
        # Paper CITES Paper
        assert OntologyValidator.validate_edge("Paper", "CITES", "Paper") == True
        # Paper USES_DATASET Dataset
        assert OntologyValidator.validate_edge("Paper", "USES_DATASET", "Dataset") == True
        # Method IMPROVES_UPON Method
        assert OntologyValidator.validate_edge("Method", "IMPROVES_UPON", "Method") == True

    def test_validate_invalid_edges(self):
        """Test validation of invalid edge relationships."""
        # Paper CITES Method (wrong target)
        assert OntologyValidator.validate_edge("Paper", "CITES", "Method") == False
        # Dataset USES_DATASET Paper (wrong source)
        assert OntologyValidator.validate_edge("Dataset", "USES_DATASET", "Paper") == False
        # Invalid types
        assert OntologyValidator.validate_edge("Invalid", "CITES", "Paper") == False

    def test_get_allowed_targets(self):
        """Test getting allowed targets for a source-edge pair."""
        # Paper CITES -> {Paper}
        targets = OntologyValidator.get_allowed_targets("Paper", "CITES")
        assert targets == {"Paper"}

        # Paper USES_DATASET -> {Dataset}
        targets = OntologyValidator.get_allowed_targets("Paper", "USES_DATASET")
        assert targets == {"Dataset"}

        # Method IMPROVES_UPON -> {Method}
        targets = OntologyValidator.get_allowed_targets("Method", "IMPROVES_UPON")
        assert targets == {"Method"}

    def test_get_allowed_targets_invalid_input(self):
        """Test get_allowed_targets with invalid input."""
        assert OntologyValidator.get_allowed_targets("Invalid", "CITES") == set()
        assert OntologyValidator.get_allowed_targets("Paper", "INVALID_EDGE") == set()


class TestNodeClass:
    """Test Node class."""

    def test_node_creation_valid(self):
        """Test creating a valid node."""
        node = Node(
            node_id="paper_001_method_001",
            node_type="Method",
            name="Transformer",
            paper_id="paper_001",
            properties={"introduced_year": 2017},
        )
        assert node.node_id == "paper_001_method_001"
        assert node.node_type == "Method"
        assert node.name == "Transformer"
        assert node.paper_id == "paper_001"
        assert node.properties["introduced_year"] == 2017

    def test_node_creation_invalid_type(self):
        """Test creating a node with invalid type."""
        with pytest.raises(ValueError, match="Invalid node type"):
            Node(
                node_id="test_001",
                node_type="InvalidType",
                name="Test",
                paper_id="paper_001",
            )

    def test_node_to_dict(self):
        """Test node serialization to dictionary."""
        node = Node(
            node_id="node_001",
            node_type="Dataset",
            name="ImageNet",
            paper_id="paper_001",
        )
        result = node.to_dict()
        assert result["node_id"] == "node_001"
        assert result["node_type"] == "Dataset"
        assert result["name"] == "ImageNet"
        assert result["paper_id"] == "paper_001"
        assert isinstance(result["properties"], dict)

    def test_node_default_properties(self):
        """Test node creation with default empty properties."""
        node = Node(
            node_id="test",
            node_type="Paper",
            name="Test Paper",
            paper_id="paper_001",
        )
        assert node.properties == {}


class TestEdgeClass:
    """Test Edge class."""

    def test_edge_creation_valid(self):
        """Test creating a valid edge."""
        edge = Edge(
            source_id="paper_001",
            source_type="Paper",
            edge_type="CITES",
            target_id="paper_002",
            target_type="Paper",
            evidence="This work builds on previous research...",
            confidence=0.95,
        )
        assert edge.source_id == "paper_001"
        assert edge.edge_type == "CITES"
        assert edge.target_id == "paper_002"
        assert edge.confidence == 0.95
        assert edge.evidence is not None

    def test_edge_creation_invalid_relationship(self):
        """Test creating an edge with invalid relationship."""
        with pytest.raises(ValueError, match="Invalid edge"):
            Edge(
                source_id="paper_001",
                source_type="Paper",
                edge_type="CITES",
                target_id="method_001",
                target_type="Method",  # Paper CITES Method is invalid
            )

    def test_edge_confidence_clamping(self):
        """Test that confidence is clamped to [0, 1]."""
        # Test values > 1
        edge1 = Edge(
            source_id="s",
            source_type="Paper",
            edge_type="CITES",
            target_id="t",
            target_type="Paper",
            confidence=2.0,
        )
        assert edge1.confidence == 1.0

        # Test values < 0
        edge2 = Edge(
            source_id="s",
            source_type="Paper",
            edge_type="CITES",
            target_id="t",
            target_type="Paper",
            confidence=-0.5,
        )
        assert edge2.confidence == 0.0

        # Test valid values
        edge3 = Edge(
            source_id="s",
            source_type="Paper",
            edge_type="CITES",
            target_id="t",
            target_type="Paper",
            confidence=0.5,
        )
        assert edge3.confidence == 0.5

    def test_edge_to_dict(self):
        """Test edge serialization to dictionary."""
        edge = Edge(
            source_id="method_001",
            source_type="Method",
            edge_type="IMPROVES_UPON",
            target_id="method_002",
            target_type="Method",
            evidence="Achieves higher accuracy",
            confidence=0.8,
        )
        result = edge.to_dict()
        assert result["source_id"] == "method_001"
        assert result["edge_type"] == "IMPROVES_UPON"
        assert result["target_id"] == "method_002"
        assert result["confidence"] == 0.8
        assert result["evidence"] == "Achieves higher accuracy"

    def test_edge_default_values(self):
        """Test edge creation with default values."""
        edge = Edge(
            source_id="s",
            source_type="Paper",
            edge_type="CITES",
            target_id="t",
            target_type="Paper",
        )
        assert edge.evidence is None
        assert edge.confidence == 1.0
        assert edge.properties == {}


class TestOntologyFunctions:
    """Test utility functions."""

    def test_get_all_node_types(self):
        """Test getting all node types."""
        types = get_all_node_types()
        assert isinstance(types, list)
        assert len(types) == 10
        assert "Paper" in types
        assert "Method" in types

    def test_get_all_edge_types(self):
        """Test getting all edge types."""
        types = get_all_edge_types()
        assert isinstance(types, list)
        assert len(types) > 15
        assert "CITES" in types
        assert "USES_DATASET" in types

    def test_describe_ontology(self):
        """Test ontology description output."""
        ontology = describe_ontology()
        assert "node_types" in ontology
        assert "edge_types" in ontology
        assert "valid_edges" in ontology
        assert isinstance(ontology["node_types"], list)
        assert isinstance(ontology["edge_types"], list)
        assert isinstance(ontology["valid_edges"], list)


class TestComplexScenarios:
    """Test complex graph construction scenarios."""

    def test_paper_citation_graph(self):
        """Test building a simple paper citation graph."""
        # Create papers
        paper1 = Node("p1", "Paper", "Attention Is All You Need", "p1")
        paper2 = Node("p2", "Paper", "BERT", "p2")

        # Create citation edge
        citation = Edge(
            source_id="p2",
            source_type="Paper",
            edge_type="CITES",
            target_id="p1",
            target_type="Paper",
            evidence="BERT builds on transformer architecture",
            confidence=1.0,
        )

        assert citation.source_id == "p2"
        assert citation.target_id == "p1"
        assert citation.confidence == 1.0

    def test_method_improvement_chain(self):
        """Test creating a chain of method improvements."""
        method1 = Node("m1", "Method", "RNN", "p1")
        method2 = Node("m2", "Method", "LSTM", "p2")
        method3 = Node("m3", "Method", "Transformer", "p3")

        # LSTM improves on RNN
        edge1 = Edge(
            source_id="m2",
            source_type="Method",
            edge_type="IMPROVES_UPON",
            target_id="m1",
            target_type="Method",
        )

        # Transformer improves on LSTM
        edge2 = Edge(
            source_id="m3",
            source_type="Method",
            edge_type="IMPROVES_UPON",
            target_id="m2",
            target_type="Method",
        )

        assert edge1.source_type == "Method"
        assert edge2.target_type == "Method"

    def test_paper_with_sections_and_claims(self):
        """Test creating a paper with sections containing claims."""
        paper = Node("p1", "Paper", "Research Paper", "p1")
        intro_section = Node("p1_s1", "Section", "Introduction", "p1")
        claim1 = Node("p1_c1", "Claim", "Current methods are inefficient", "p1")

        # Paper has Introduction section
        paper_has_section = Edge(
            source_id="p1",
            source_type="Paper",
            edge_type="HAS_SECTION",
            target_id="p1_s1",
            target_type="Section",
        )

        # Section contains claim
        section_contains_claim = Edge(
            source_id="p1_s1",
            source_type="Section",
            edge_type="CONTAINS_CLAIM",
            target_id="p1_c1",
            target_type="Claim",
        )

        assert paper_has_section.edge_type == "HAS_SECTION"
        assert section_contains_claim.edge_type == "CONTAINS_CLAIM"
