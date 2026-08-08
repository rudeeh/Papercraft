"""Tests for app/storage/object_store.py."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.storage.object_store import (
    ObjectStore,
    ObjectStoreError,
    create_object_store,
    pdf_key,
)


def _store(client=None) -> ObjectStore:
    store = ObjectStore(
        endpoint_url="http://minio:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="papercraft-pdfs",
    )
    store._client = client or MagicMock()
    return store


class TestKeys:
    def test_pdf_key_is_namespaced(self):
        assert pdf_key("abc123") == "pdfs/abc123.pdf"


class TestLifecycle:
    def test_using_the_client_before_connect_is_an_error(self):
        store = ObjectStore("http://minio:9000", "a", "b", "bucket")
        with pytest.raises(ObjectStoreError, match="connect"):
            _ = store.client

    def test_close_releases_the_client(self):
        store = _store()
        store.close()
        with pytest.raises(ObjectStoreError):
            _ = store.client


class TestEnsureBucket:
    def test_existing_bucket_is_not_recreated(self):
        client = MagicMock()
        _store(client).ensure_bucket()
        client.create_bucket.assert_not_called()

    def test_missing_bucket_is_created(self):
        client = MagicMock()
        client.head_bucket.side_effect = Exception("404")
        _store(client).ensure_bucket()
        client.create_bucket.assert_called_once()

    def test_us_east_1_omits_the_location_constraint(self):
        """Passing one in us-east-1 is an error rather than a requirement."""
        client = MagicMock()
        client.head_bucket.side_effect = Exception("404")
        _store(client).ensure_bucket()
        assert "CreateBucketConfiguration" not in client.create_bucket.call_args.kwargs

    def test_other_regions_include_the_location_constraint(self):
        client = MagicMock()
        client.head_bucket.side_effect = Exception("404")
        store = ObjectStore("http://minio:9000", "a", "b", "bucket", region="eu-west-1")
        store._client = client
        store.ensure_bucket()
        assert client.create_bucket.call_args.kwargs["CreateBucketConfiguration"] == {
            "LocationConstraint": "eu-west-1"
        }

    def test_a_concurrent_creation_is_tolerated(self):
        client = MagicMock()
        client.head_bucket.side_effect = Exception("404")
        client.create_bucket.side_effect = Exception("BucketAlreadyOwnedByYou")
        _store(client).ensure_bucket()  # must not raise

    def test_a_real_failure_is_reported(self):
        client = MagicMock()
        client.head_bucket.side_effect = Exception("404")
        client.create_bucket.side_effect = Exception("AccessDenied")
        with pytest.raises(ObjectStoreError, match="AccessDenied"):
            _store(client).ensure_bucket()


class TestObjectOperations:
    def test_put_file_sets_the_content_type(self):
        client = MagicMock()
        _store(client).put_file("pdfs/a.pdf", "/tmp/a.pdf")
        assert client.upload_file.call_args.kwargs["ExtraArgs"] == {
            "ContentType": "application/pdf"
        }

    def test_put_file_failure_is_wrapped(self):
        client = MagicMock()
        client.upload_file.side_effect = Exception("connection reset")
        with pytest.raises(ObjectStoreError, match="Upload of 'pdfs/a.pdf' failed"):
            _store(client).put_file("pdfs/a.pdf", "/tmp/a.pdf")

    def test_get_to_file_returns_the_destination(self):
        assert _store().get_to_file("pdfs/a.pdf", "/tmp/a.pdf") == "/tmp/a.pdf"

    def test_exists_is_false_rather_than_raising(self):
        client = MagicMock()
        client.head_object.side_effect = Exception("404")
        assert _store(client).exists("pdfs/missing.pdf") is False

    def test_exists_is_true_for_a_present_object(self):
        assert _store().exists("pdfs/a.pdf") is True

    def test_presigned_url_is_returned(self):
        client = MagicMock()
        client.generate_presigned_url.return_value = "http://minio:9000/signed"
        assert _store(client).presigned_url("pdfs/a.pdf") == "http://minio:9000/signed"

    def test_list_keys_walks_every_page(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "pdfs/a.pdf"}]},
            {"Contents": [{"Key": "pdfs/b.pdf"}]},
            {},  # a final empty page is normal
        ]
        assert _store(client).list_keys("pdfs/") == ["pdfs/a.pdf", "pdfs/b.pdf"]


class TestCreateObjectStore:
    def test_returns_none_when_disabled(self):
        cfg = SimpleNamespace(OBJECT_STORE_ENABLED=False)
        assert create_object_store(cfg) is None

    def test_returns_none_when_unreachable(self, monkeypatch):
        """An unreachable MinIO degrades to local disk; it must not raise."""
        cfg = SimpleNamespace(
            OBJECT_STORE_ENABLED=True,
            MINIO_ENDPOINT="http://nope:9000",
            MINIO_ACCESS_KEY="a",
            MINIO_SECRET_KEY="b",
            MINIO_BUCKET_PDFS="bucket",
            MINIO_REGION="us-east-1",
        )
        monkeypatch.setattr(
            ObjectStore,
            "connect",
            lambda self: (_ for _ in ()).throw(ObjectStoreError("no route to host")),
        )
        assert create_object_store(cfg) is None
