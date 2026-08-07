"""Tests for PaperGraphBuilder (Phase 7)."""

import pytest
from app.graph.paper_graph_builder import PaperGraphBuilder
from app.graph.ontology import OntologyValidator


@pytest.fixture
def builder():
    return PaperGraphBuilder()


class TestPaperGraphBuilder:
    def test_build_minimal_paper(self, builder):
        """A paper with just a title should produce exactly 1 node (Paper)."""
        result = builder.build(paper_id="test1", title="Test Paper")
        assert len(result["nodes"]) == 1
        assert result["nodes"][0].node_type == "Paper"
        assert result["nodes"][0].name == "Test Paper"
        assert len(result["edges"]) == 0

    def test_build_with_authors(self, builder):
        """Paper + 2 authors should produce 3 nodes and 2 WRITTEN_BY edges."""
        result = builder.build(
            paper_id="test2",
            title="Authored Paper",
            authors=["Alice Smith", "Bob Jones"],
        )
        node_types = {n.node_type for n in result["nodes"]}
        assert node_types == {"Paper", "Author"}
        assert len(result["nodes"]) == 3
        assert len(result["edges"]) == 2
        edge_types = {e.edge_type for e in result["edges"]}
        assert edge_types == {"WRITTEN_BY"}

    def test_build_with_sections(self, builder):
        """Sections produce Section nodes with HAS_SECTION edges."""
        result = builder.build(
            paper_id="test3",
            title="Sectored Paper",
            sections=[
                {"heading": "Abstract", "text": "We propose..."},
                {"heading": "Introduction", "text": "Background..."},
            ],
        )
        section_nodes = [n for n in result["nodes"] if n.node_type == "Section"]
        assert len(section_nodes) == 2
        has_section_edges = [e for e in result["edges"] if e.edge_type == "HAS_SECTION"]
        assert len(has_section_edges) == 2

    def test_build_with_entities(self, builder):
        """Entity nodes are created and linked via MENTIONS edges."""
        entities = [
            {"name": "BERT", "type": "Method", "source_section": "Abstract", "evidence": "We use BERT"},
            {"name": "SQuAD", "type": "Dataset", "source_section": "Experiments", "evidence": "Evaluated on SQuAD"},
        ]
        result = builder.build(paper_id="test4", title="Entity Paper", entities=entities)
        entity_nodes = [n for n in result["nodes"] if n.node_type in ("Method", "Dataset")]
        assert len(entity_nodes) == 2
        mention_edges = [e for e in result["edges"] if e.edge_type == "MENTIONS"]
        assert len(mention_edges) == 2

    def test_entity_deduplication(self, builder):
        """Duplicate entities (same type+name) are deduplicated."""
        entities = [
            {"name": "BERT", "type": "Method", "source_section": "Abstract", "evidence": "e1"},
            {"name": "BERT", "type": "Method", "source_section": "Intro", "evidence": "e2"},
            {"name": "bert", "type": "Method", "source_section": "Intro", "evidence": "e3"},
        ]
        result = builder.build(paper_id="test5", title="Dedup Paper", entities=entities)
        method_nodes = [n for n in result["nodes"] if n.node_type == "Method"]
        assert len(method_nodes) == 1

    def test_citation_stubs_created(self, builder):
        """Citations create stub Paper nodes and CITES edges."""
        citations = [
            {"title": "Attention Is All You Need", "year": 2017, "ref_id": "ref_1"},
            {"doi": "10.1234/test.5678", "title": "DOI Paper", "ref_id": "ref_2"},
            {"arxiv_id": "2301.00001", "title": "ArXiv Paper", "ref_id": "ref_3"},
        ]
        result = builder.build(paper_id="test6", title="Citing Paper", citations=citations)
        paper_nodes = [n for n in result["nodes"] if n.node_type == "Paper"]
        assert len(paper_nodes) == 4  # 1 real + 3 stubs
        cites_edges = [e for e in result["edges"] if e.edge_type == "CITES"]
        assert len(cites_edges) == 3

    def test_citation_stub_stable_id_doi(self, builder):
        """Citation with DOI gets a stable ID based on the DOI."""
        citations = [{"doi": "10.1234/test.5678", "title": "Paper", "ref_id": "r1"}]
        result = builder.build(paper_id="p1", title="P", citations=citations)
        stub = [n for n in result["nodes"] if n.properties.get("is_stub")]
        assert len(stub) == 1
        assert stub[0].paper_id == "doi_10.1234/test.5678"

    def test_citation_stub_stable_id_arxiv(self, builder):
        """Citation with arXiv ID gets a stable ID based on arXiv."""
        citations = [{"arxiv_id": "2301.00001", "title": "Paper", "ref_id": "r1"}]
        result = builder.build(paper_id="p1", title="P", citations=citations)
        stub = [n for n in result["nodes"] if n.properties.get("is_stub")]
        assert len(stub) == 1
        assert stub[0].paper_id == "arxiv_2301.00001"

    def test_citation_without_title_or_id_skipped(self, builder):
        """Citations with no title, DOI, or arXiv are skipped."""
        citations = [{"year": 2020, "ref_id": "r1"}]
        result = builder.build(paper_id="p1", title="P", citations=citations)
        stubs = [n for n in result["nodes"] if n.properties.get("is_stub")]
        assert len(stubs) == 0

    def test_invalid_entity_type_rejected(self, builder):
        """Entities with invalid types are silently dropped."""
        entities = [
            {"name": "Something", "type": "InvalidType", "source_section": "A", "evidence": "e"},
        ]
        result = builder.build(paper_id="p1", title="P", entities=entities)
        assert len(result["nodes"]) == 1  # Only Paper node

    def test_relation_edges_between_entities(self, builder):
        """Relation edges connect entity nodes."""
        entities = [
            {"name": "BERT", "type": "Method", "source_section": "A", "evidence": "e1"},
            {"name": "GPT", "type": "Method", "source_section": "A", "evidence": "e2"},
        ]
        relations = [
            {
                "source": "BERT", "source_type": "Method",
                "relation": "IMPROVES_UPON",
                "target": "GPT", "target_type": "Method",
                "evidence": "BERT outperforms GPT",
            }
        ]
        result = builder.build(paper_id="p1", title="P", entities=entities, relations=relations)
        rel_edges = [e for e in result["edges"] if e.edge_type == "IMPROVES_UPON"]
        assert len(rel_edges) == 1

    def test_edge_deduplication(self, builder):
        """Duplicate edges are removed."""
        entities = [
            {"name": "BERT", "type": "Method", "source_section": "A", "evidence": "e1"},
            {"name": "GPT", "type": "Method", "source_section": "A", "evidence": "e2"},
        ]
        relations = [
            {"source": "BERT", "source_type": "Method", "relation": "COMPARES_TO",
             "target": "GPT", "target_type": "Method", "evidence": "e"},
            {"source": "BERT", "source_type": "Method", "relation": "COMPARES_TO",
             "target": "GPT", "target_type": "Method", "evidence": "e"},
        ]
        result = builder.build(paper_id="p1", title="P", entities=entities, relations=relations)
        compare_edges = [e for e in result["edges"] if e.edge_type == "COMPARES_TO"]
        assert len(compare_edges) == 1

    def test_full_pipeline_build(self, builder):
        """Full build with all inputs produces a rich graph."""
        entities = [
            {"name": "Transformer", "type": "Method", "source_section": "Abstract", "evidence": "proposes Transformer"},
        ]
        citations = [
            {"title": "Old Paper", "doi": "10.1/old", "ref_id": "ref_1"},
        ]
        result = builder.build(
            paper_id="full1",
            title="Full Paper",
            abstract="We propose a Transformer model.",
            authors=["Alice"],
            year=2024,
            sections=[{"heading": "Abstract", "text": "We propose a Transformer model."}],
            entities=entities,
            citations=citations,
        )
        assert len(result["nodes"]) >= 4  # Paper, Author, Section, Method, Stub
        assert len(result["edges"]) >= 3  # WRITTEN_BY, HAS_SECTION, MENTIONS, CITES