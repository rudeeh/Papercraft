"""
Tests for GraphRepository.store_curated_entity / store_curated_relation --
the path a promoted draft takes into Neo4j.
"""

from unittest.mock import MagicMock

import pytest

from app.graph.paper_graph_builder import PaperGraphBuilder, build_entity_node
from app.storage.graph_repository import GraphRepository

ENTITY = {
    "name": "Transformer",
    "type": "Method",
    "source_section": "Abstract",
    "evidence": "The Transformer is the first transduction model relying entirely on attention.",
}

RELATION = {
    "source": "Transformer",
    "source_type": "Method",
    "relation": "USES_DATASET",
    "target": "WMT14",
    "target_type": "Dataset",
    "evidence": "The Transformer is evaluated on WMT14.",
}


@pytest.fixture
def repo():
    return GraphRepository(MagicMock())


def merged_nodes(repo):
    return {
        call.args[0]: call.args[1] for call in repo._client.merge_node.call_args_list
    }


class TestStoreCuratedEntity:
    def test_node_id_matches_what_the_pipeline_would_have_built(self, repo):
        repo.store_curated_entity("paper_1", ENTITY)
        expected = build_entity_node("paper_1", ENTITY).node_id
        method_props = [
            c.args[1] for c in repo._client.merge_node.call_args_list if c.args[0] == "Method"
        ][0]
        assert method_props["node_id"] == expected

    def test_promoting_merges_onto_the_pipelines_node_not_a_duplicate(self, repo):
        """
        A curated 'Transformer' and a pipeline-extracted 'Transformer' must
        be the same node, or the graph quietly grows twins.
        """
        pipeline_graph = PaperGraphBuilder().build(paper_id="paper_1", entities=[ENTITY])
        pipeline_ids = {n.node_id for n in pipeline_graph["nodes"]}

        repo.store_curated_entity("paper_1", ENTITY)
        curated_id = [
            c.args[1]["node_id"]
            for c in repo._client.merge_node.call_args_list
            if c.args[0] == "Method"
        ][0]
        assert curated_id in pipeline_ids

    def test_paper_node_is_merged_by_id_only(self, repo):
        """
        Passing the Paper's other properties would SET them on match, so a
        promotion carrying no title would blank the real paper's title.
        """
        repo.store_curated_entity("paper_1", ENTITY)
        paper_props = merged_nodes(repo)["Paper"]
        assert paper_props == {"node_id": "paper_paper_1"}

    def test_entity_is_flagged_as_curated(self, repo):
        repo.store_curated_entity("paper_1", ENTITY, curated_by="user-1")
        props = merged_nodes(repo)["Method"]
        assert props["curated"] is True
        assert props["curated_by"] == "user-1"

    def test_edge_uses_the_extracted_role_when_the_ontology_allows_it(self, repo):
        repo.store_curated_entity("paper_1", {**ENTITY, "role": "introduces"})
        assert repo._client.merge_edge.call_args.kwargs["edge_type"] == "INTRODUCES"

    def test_edge_falls_back_to_mentions_when_the_role_is_not_allowed(self, repo):
        # Paper -INTRODUCES-> Metric is not in the ontology.
        repo.store_curated_entity(
            "paper_1", {"name": "BLEU", "type": "Metric", "role": "introduces", "evidence": "x"}
        )
        assert repo._client.merge_edge.call_args.kwargs["edge_type"] == "MENTIONS"

    def test_default_edge_is_mentions(self, repo):
        repo.store_curated_entity("paper_1", ENTITY)
        assert repo._client.merge_edge.call_args.kwargs["edge_type"] == "MENTIONS"

    @pytest.mark.parametrize(
        "bad", [{"name": "", "type": "Method"}, {"name": "X", "type": "Vibe"}, {}]
    )
    def test_invalid_entities_are_rejected_loudly(self, repo, bad):
        with pytest.raises(ValueError, match="ontology"):
            repo.store_curated_entity("paper_1", bad)


class TestStoreCuratedRelation:
    def test_both_endpoints_are_merged_before_the_edge(self, repo):
        repo.store_curated_relation("paper_1", RELATION)
        labels = [c.args[0] for c in repo._client.merge_node.call_args_list]
        assert labels == ["Method", "Dataset"]
        repo._client.merge_edge.assert_called_once()

    def test_endpoint_ids_match_the_pipelines_scheme(self, repo):
        repo.store_curated_relation("paper_1", RELATION)
        kwargs = repo._client.merge_edge.call_args.kwargs
        assert kwargs["source_key_value"] == build_entity_node(
            "paper_1", {"name": "Transformer", "type": "Method"}
        ).node_id
        assert kwargs["target_key_value"] == build_entity_node(
            "paper_1", {"name": "WMT14", "type": "Dataset"}
        ).node_id

    def test_edge_carries_evidence_and_the_curated_flag(self, repo):
        repo.store_curated_relation("paper_1", RELATION, curated_by="user-1")
        props = repo._client.merge_edge.call_args.kwargs["edge_properties"]
        assert props["curated"] is True
        assert props["evidence"] == RELATION["evidence"]
        assert props["curated_by"] == "user-1"

    def test_an_edge_the_ontology_forbids_is_rejected(self, repo):
        with pytest.raises(ValueError, match="ontology"):
            repo.store_curated_relation("paper_1", {**RELATION, "relation": "WRITTEN_BY"})

    def test_an_invented_relation_type_is_rejected(self, repo):
        with pytest.raises(ValueError, match="ontology"):
            repo.store_curated_relation("paper_1", {**RELATION, "relation": "VIBES_WITH"})

    def test_a_nameless_endpoint_is_rejected(self, repo):
        with pytest.raises(ValueError):
            repo.store_curated_relation("paper_1", {**RELATION, "target": ""})
