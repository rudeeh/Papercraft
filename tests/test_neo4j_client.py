"""Tests for Neo4jClient (Phase 8)."""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from app.storage.neo4j_client import Neo4jClient, NODE_KEY_MAP


class TestNeo4jClientInit:
    def test_init_params_stored(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw", database="neo4j")
        assert client._uri == "bolt://localhost:7687"
        assert client._user == "neo4j"
        assert client._password == "pw"
        assert client._database == "neo4j"
        assert client._driver is None

    def test_node_key_map_completeness(self):
        """Every ontology node type should have a key property."""
        from app.graph.ontology import NodeType
        for nt in NodeType:
            assert nt.value in NODE_KEY_MAP, f"Missing key for {nt.value}"


class TestNeo4jClientSchema:
    def test_init_schema_creates_constraints(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")

        mock_session = MagicMock()
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.verify_connectivity = MagicMock()

        with patch("app.storage.neo4j_client.GraphDatabase") as mock_gd:
            mock_gd.driver.return_value = mock_driver
            client.connect()
            client.init_schema()

        # Should have run 10 constraint creations + 4 index creations = 14 calls
        assert mock_session.run.call_count >= 10


class TestNeo4jClientMergeNode:
    def test_merge_node_basic(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")

        mock_session = MagicMock()
        mock_record = MagicMock()
        mock_record.__getitem__ = lambda self, key: {"paper_id": "p1", "title": "Test"}
        mock_session.run.return_value.single.return_value = mock_record

        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.verify_connectivity = MagicMock()

        with patch("app.storage.neo4j_client.GraphDatabase") as mock_gd:
            mock_gd.driver.return_value = mock_driver
            client.connect()
            result = client.merge_node("Paper", {"node_id": "paper_p1", "paper_id": "p1", "title": "Test"})

        assert mock_session.run.called
        # Verify MERGE is used (not CREATE)
        cypher_called = mock_session.run.call_args[0][0]
        assert "MERGE" in cypher_called
        assert "ON CREATE SET" in cypher_called
        assert "ON MATCH SET" in cypher_called

    def test_merge_node_missing_key_raises(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")
        client._driver = MagicMock()  # Pretend connected
        with pytest.raises(ValueError, match="missing 'node_id' property"):
            client.merge_node("Paper", {"title": "Test"})


class TestNeo4jClientMergeEdge:
    def test_merge_edge_basic(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")

        mock_session = MagicMock()
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.verify_connectivity = MagicMock()

        with patch("app.storage.neo4j_client.GraphDatabase") as mock_gd:
            mock_gd.driver.return_value = mock_driver
            client.connect()
            client.merge_edge(
                source_label="Paper", source_key_prop="paper_id", source_key_value="p1",
                target_label="Method", target_key_prop="name", target_key_value="BERT",
                edge_type="INTRODUCES",
            )

        cypher_called = mock_session.run.call_args[0][0]
        assert "MERGE" in cypher_called
        assert "INTRODUCES" in cypher_called


class TestNeo4jClientHealthCheck:
    def test_health_check_success(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")
        mock_session = MagicMock()
        mock_session.run.return_value = [{"health": 1}]
        mock_driver = MagicMock()
        mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
        mock_driver.verify_connectivity = MagicMock()

        with patch("app.storage.neo4j_client.GraphDatabase") as mock_gd:
            mock_gd.driver.return_value = mock_driver
            client.connect()
            assert client.health_check() is True

    def test_health_check_failure(self):
        client = Neo4jClient(uri="bolt://localhost:7687", user="neo4j", password="pw")
        client._driver = None  # No connection
        assert client.health_check() is False