"""
Integration tests for Phase 1-3 components.

Tests validate:
- Complete OCR -> Parser -> Graph input pipeline
- Component compatibility
- Data flow between modules
"""

import pytest
from app.paper.parser import PaperParser, PaperParseResult
from app.graph.ontology import (
    NodeType,
    EdgeType,
    OntologyValidator,
    Node,
    Edge,
)


class TestOCRToParserIntegration:
    """Test integration between OCR output and parser."""

    def test_ocr_output_format_compatibility(self):
        """
        Simulate OCR output format and verify parser handles it.

        OCR returns: List[Tuple[page_num, text]]
        """
        parser = PaperParser()

        # Simulate OCR output (same format as app/services/ocr.py extract_text_from_pdf)
        ocr_output = [
            (1, "Attention Is All You Need\\n\\nAbstract\\nThis paper introduces..."),
            (2, "Introduction\\nRecurrent neural networks have been the standard..."),
            (3, "Related Work\\nMany attention mechanisms have been proposed..."),
        ]

        result = parser.parse(ocr_output)

        # Verify parser output is compatible with downstream processing
        assert isinstance(result, PaperParseResult)
        assert result.title is not None
        assert len(result.raw_pages) == 3

    def test_parser_preserves_page_information(self):
        """Test that parser preserves page numbering from OCR."""
        parser = PaperParser()
        ocr_output = [
            (1, "Content page 1"),
            (2, "Content page 2"),
            (5, "Content page 5"),  # Non-sequential
        ]

        result = parser.parse(ocr_output)

        # Raw pages should be preserved exactly
        assert result.raw_pages == ocr_output
        assert result.raw_pages[0][0] == 1
        assert result.raw_pages[2][0] == 5


class TestParserToGraphOntologyIntegration:
    """Test compatibility between parser output and graph ontology."""

    def test_paper_node_from_parsed_title(self):
        """Test creating Paper node from parser's extracted title."""
        parser = PaperParser()
        text = "Attention Is All You Need\\n\\nAbstract\\nTransformer paper..."
        result = parser.parse([(1, text)])

        # Create Paper node from parsed data
        paper_node = Node(
            node_id="arxiv_1706_03762",  # Would come from metadata
            node_type="Paper",
            name=result.title or "Unknown",
            paper_id="arxiv_1706_03762",
            properties={"abstract": result.abstract},
        )

        assert paper_node.node_type == NodeType.PAPER.value
        assert paper_node.name == result.title

    def test_section_nodes_from_parsed_sections(self):
        """Test creating Section nodes from parsed sections."""
        parser = PaperParser()
        text = """
        Paper Title

        Abstract
        Abstract content.

        Introduction
        Intro content.

        Methods
        Method content.
        """
        result = parser.parse([(1, text)])

        # Create Section nodes from parsed sections
        section_nodes = []
        for i, section in enumerate(result.sections):
            node = Node(
                node_id=f"section_{i}",
                node_type="Section",
                name=section["heading"],
                paper_id="arxiv_001",
                properties={"content_preview": section["text"][:100]},
            )
            section_nodes.append(node)
            # Verify ontology allows this node type
            assert OntologyValidator.validate_node_type(node.node_type)

        # Should have created nodes
        assert len(section_nodes) > 0

    def test_section_containment_edges(self):
        """Test creating HAS_SECTION edges between paper and sections."""
        # Create Paper node
        paper = Node("p1", "Paper", "Test Paper", "p1")

        # Create Section node
        section = Node("p1_s1", "Section", "Introduction", "p1")

        # Create HAS_SECTION edge
        edge = Edge(
            source_id=paper.node_id,
            source_type=paper.node_type,
            edge_type="HAS_SECTION",
            target_id=section.node_id,
            target_type=section.node_type,
        )

        # Verify this is a valid edge
        assert OntologyValidator.validate_edge(
            paper.node_type,
            edge.edge_type,
            section.node_type,
        )

    def test_parser_output_structure_matches_graph_input(self):
        """Test that parser output structure is compatible with graph construction."""
        parser = PaperParser()
        text = """
        Title

        Abstract
        Abstract text.

        Introduction
        Introduction text.
        """
        result = parser.parse([(1, text)])

        # Verify all required fields for graph input exist
        assert hasattr(result, "title")
        assert hasattr(result, "abstract")
        assert hasattr(result, "sections")
        assert hasattr(result, "references")
        assert hasattr(result, "raw_pages")

        # Verify structure is correct
        for section in result.sections:
            assert isinstance(section, dict)
            assert "heading" in section
            assert "text" in section

        for ref in result.references:
            assert isinstance(ref, dict)


class TestOntologyWithParsedData:
    """Test ontology validation with real parsed data."""

    def test_validate_paper_with_method_relationship(self):
        """Test creating valid relationship between parsed paper and method."""
        paper = Node("p1", "Paper", "ML Paper", "p1")
        method = Node("m1", "Method", "Neural Network", "p1")

        # Paper INTRODUCES Method
        edge = Edge(
            source_id=paper.node_id,
            source_type=paper.node_type,
            edge_type="INTRODUCES",
            target_id=method.node_id,
            target_type=method.node_type,
        )

        assert edge.edge_type == "INTRODUCES"
        assert OntologyValidator.validate_edge(
            paper.node_type,
            edge.edge_type,
            method.node_type,
        )

    def test_validate_paper_with_dataset_relationship(self):
        """Test creating valid relationship between paper and dataset."""
        paper = Node("p1", "Paper", "Benchmark Paper", "p1")
        dataset = Node("d1", "Dataset", "ImageNet", "p1")

        # Paper USES_DATASET Dataset
        edge = Edge(
            source_id=paper.node_id,
            source_type=paper.node_type,
            edge_type="USES_DATASET",
            target_id=dataset.node_id,
            target_type=dataset.node_type,
        )

        assert OntologyValidator.validate_edge(
            paper.node_type,
            edge.edge_type,
            dataset.node_type,
        )


class TestCompletePhase1To3Pipeline:
    """Test complete pipeline from OCR through graph ontology."""

    def test_end_to_end_ocr_to_graph(self):
        """
        Complete end-to-end test:
        OCR Output -> Parser -> Graph Nodes & Edges
        """
        # Step 1: Simulate OCR output
        ocr_output = [
            (1, """
            Transformer: A Novel Architecture for NLP

            Abstract
            We propose a new network architecture based entirely on attention mechanisms.
            The Transformer model achieves state-of-the-art results on translation tasks.

            Introduction
            Recurrent neural networks have dominated sequence modeling. However, RNNs
            suffer from limited parallelization. We introduce the Transformer.

            Methods
            Our model uses multi-head self-attention. We stack multiple layers.

            Experiments
            We evaluate on WMT14 English-German translation. Results show
            significant improvement over previous baselines.

            References
            [1] Bahdanau et al., 2015. Neural Machine Translation. arXiv:1409.0473
            [2] Gehring et al., 2017. Convolutional. arXiv:1705.03122
            """),
        ]

        # Step 2: Parse OCR output
        parser = PaperParser()
        parsed = parser.parse(ocr_output)

        assert parsed.title is not None
        assert parsed.abstract is not None
        assert len(parsed.sections) > 0
        assert len(parsed.references) > 0

        # Step 3: Create graph nodes from parsed data
        paper_node = Node(
            node_id="paper_001",
            node_type="Paper",
            name=parsed.title,
            paper_id="paper_001",
            properties={"abstract": parsed.abstract},
        )

        # Verify Paper node is valid
        assert OntologyValidator.validate_node_type(paper_node.node_type)

        # Step 4: Create section nodes
        section_nodes = []
        for section in parsed.sections:
            section_node = Node(
                node_id=f"section_{len(section_nodes)}",
                node_type="Section",
                name=section["heading"],
                paper_id="paper_001",
            )
            section_nodes.append(section_node)

        # Step 5: Create edges
        edges = []
        for section_node in section_nodes:
            edge = Edge(
                source_id=paper_node.node_id,
                source_type=paper_node.node_type,
                edge_type="HAS_SECTION",
                target_id=section_node.node_id,
                target_type=section_node.node_type,
            )
            edges.append(edge)

        # Verify all edges are valid
        for edge in edges:
            assert OntologyValidator.validate_edge(
                edge.source_type,
                edge.edge_type,
                edge.target_type,
            )

        # Final verification
        assert len(section_nodes) > 0
        assert len(edges) == len(section_nodes)
