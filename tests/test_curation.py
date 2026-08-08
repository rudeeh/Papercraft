"""Tests for app/services/curation.py -- the draft queue and attestation."""

from unittest.mock import MagicMock

import pytest

from app.db.models import (
    Attestation,
    AuditLog,
    DraftKind,
    DraftStatus,
    ExtractionDraft,
    RoutingDecision,
)
from app.graph.confidence_router import ConfidenceRouter
from app.services import curation
from app.services.curation import CurationError

ENTITY = {
    "name": "Transformer",
    "type": "Method",
    "evidence": "The Transformer is the first transduction model relying entirely on attention.",
}


def routed(confidence=0.6, kind=DraftKind.ENTITY, payload=None):
    router = ConfidenceRouter(auto_insert_threshold=0.85, draft_threshold=0.5)
    return router.route_one({**(payload or ENTITY), "confidence": confidence}, kind)


class TestQueueDrafts:
    def test_queues_only_items_needing_review(self, db_session):
        items = [routed(0.95), routed(0.6), routed(0.2)]
        drafts = curation.queue_drafts(db_session, "paper_1", items)
        # 0.95 auto-inserts and is not queued; 0.6 (draft) and 0.2 (manual) are.
        assert len(drafts) == 2
        assert {d.routing for d in drafts} == {
            RoutingDecision.DRAFT.value,
            RoutingDecision.MANUAL.value,
        }

    def test_discarded_items_are_not_queued(self, db_session):
        no_evidence = routed(0.9, payload={"name": "X", "type": "Method", "evidence": ""})
        assert curation.queue_drafts(db_session, "paper_1", [no_evidence]) == []

    def test_payload_is_preserved_verbatim_for_replay(self, db_session):
        draft = curation.queue_drafts(db_session, "paper_1", [routed(0.6)])[0]
        assert draft.payload["name"] == "Transformer"
        assert draft.payload["evidence"] == ENTITY["evidence"]

    def test_writes_one_audit_row_for_the_batch(self, db_session):
        curation.queue_drafts(db_session, "paper_1", [routed(0.6), routed(0.4)])
        rows = db_session.query(AuditLog).filter(AuditLog.action == "drafts.queued").all()
        assert len(rows) == 1 and rows[0].detail["count"] == 2

    def test_nothing_to_queue_writes_no_audit_row(self, db_session):
        curation.queue_drafts(db_session, "paper_1", [routed(0.95)])
        assert db_session.query(AuditLog).count() == 0


class TestListAndStats:
    def test_lowest_confidence_surfaces_first(self, db_session, make_draft):
        make_draft(confidence=0.8)
        make_draft(confidence=0.3)
        make_draft(confidence=0.6)
        assert [d.confidence for d in curation.list_drafts(db_session)] == [0.3, 0.6, 0.8]

    def test_filters_by_paper(self, db_session, make_draft):
        make_draft(paper_id="paper_1")
        make_draft(paper_id="paper_2")
        assert len(curation.list_drafts(db_session, paper_id="paper_2")) == 1

    def test_filters_by_kind(self, db_session, make_draft):
        make_draft(kind=DraftKind.ENTITY)
        make_draft(kind=DraftKind.RELATION)
        assert len(curation.list_drafts(db_session, kind="relation")) == 1

    def test_limit_is_capped(self, db_session, make_draft):
        for _ in range(3):
            make_draft()
        assert len(curation.list_drafts(db_session, limit=10_000)) == 3

    def test_stats_count_by_status(self, db_session, make_draft, make_user):
        make_draft()
        rejected = make_draft()
        curation.reject_draft(db_session, rejected.id, make_user())
        stats = curation.queue_stats(db_session)
        assert stats == {"pending": 1, "promoted": 0, "rejected": 1, "total": 2}


class TestAttest:
    def test_upvote_moves_the_score(self, db_session, make_draft, make_user):
        draft = make_draft()
        result = curation.attest(db_session, draft.id, make_user(), 1, auto_promote=False)
        assert result["score"] == 1

    def test_downvote_moves_the_score_the_other_way(self, db_session, make_draft, make_user):
        draft = make_draft()
        result = curation.attest(db_session, draft.id, make_user(), -1, auto_promote=False)
        assert result["score"] == -1

    @pytest.mark.parametrize("bad_vote", [0, 2, -5, 100])
    def test_only_plus_or_minus_one_is_accepted(self, db_session, make_draft, make_user, bad_vote):
        draft = make_draft()
        with pytest.raises(CurationError, match=r"\+1 or -1"):
            curation.attest(db_session, draft.id, make_user(), bad_vote)

    def test_unknown_draft_is_an_error(self, db_session, make_user):
        with pytest.raises(CurationError, match="No such draft"):
            curation.attest(db_session, "nope", make_user(), 1)

    def test_one_user_cannot_vote_twice(self, db_session, make_draft, make_user):
        """A single curator must not be able to reach the promote threshold alone."""
        draft = make_draft()
        user = make_user()
        curation.attest(db_session, draft.id, user, 1, auto_promote=False)
        curation.attest(db_session, draft.id, user, 1, auto_promote=False)
        assert db_session.query(Attestation).filter(Attestation.draft_id == draft.id).count() == 1
        assert db_session.get(ExtractionDraft, draft.id).attestation_score == 1

    def test_changing_a_vote_swings_the_score_by_two(self, db_session, make_draft, make_user):
        draft = make_draft()
        user = make_user()
        curation.attest(db_session, draft.id, user, 1, auto_promote=False)
        result = curation.attest(db_session, draft.id, user, -1, auto_promote=False)
        assert result["score"] == -1

    def test_two_users_reach_the_promote_threshold(self, db_session, make_draft, make_user):
        draft = make_draft()
        curation.attest(db_session, draft.id, make_user("a@x.com"), 1)
        result = curation.attest(db_session, draft.id, make_user("b@x.com"), 1)
        assert result["promoted"] is True
        assert db_session.get(ExtractionDraft, draft.id).status == DraftStatus.PROMOTED.value

    def test_two_downvotes_reject(self, db_session, make_draft, make_user):
        draft = make_draft()
        curation.attest(db_session, draft.id, make_user("a@x.com"), -1)
        curation.attest(db_session, draft.id, make_user("b@x.com"), -1)
        assert db_session.get(ExtractionDraft, draft.id).status == DraftStatus.REJECTED.value

    def test_cannot_vote_on_a_resolved_draft(self, db_session, make_draft, make_user):
        draft = make_draft()
        curation.promote_draft(db_session, draft.id, make_user("a@x.com"))
        with pytest.raises(CurationError, match="already promoted"):
            curation.attest(db_session, draft.id, make_user("b@x.com"), 1)

    def test_score_is_derived_from_the_votes_that_justify_it(
        self, db_session, make_draft, make_user
    ):
        draft = make_draft()
        curation.attest(db_session, draft.id, make_user("a@x.com"), 1, auto_promote=False)
        curation.attest(db_session, draft.id, make_user("b@x.com"), -1, auto_promote=False)
        stored = db_session.get(ExtractionDraft, draft.id)
        votes = db_session.query(Attestation).filter(Attestation.draft_id == draft.id).all()
        assert stored.attestation_score == sum(v.vote for v in votes) == 0


class TestPromoteAndReject:
    def test_promote_writes_the_entity_to_the_graph(self, db_session, make_draft, make_user):
        repo = MagicMock()
        draft = make_draft(kind=DraftKind.ENTITY)
        curation.promote_draft(db_session, draft.id, make_user(), graph_repository=repo)
        repo.store_curated_entity.assert_called_once_with("paper_1", draft.payload)

    def test_promote_writes_the_relation_to_the_graph(self, db_session, make_draft, make_user):
        repo = MagicMock()
        payload = {
            "source": "Transformer",
            "source_type": "Method",
            "relation": "USES_DATASET",
            "target": "WMT14",
            "target_type": "Dataset",
            "evidence": "evaluated on WMT14",
        }
        draft = make_draft(kind=DraftKind.RELATION, payload=payload)
        curation.promote_draft(db_session, draft.id, make_user(), graph_repository=repo)
        repo.store_curated_relation.assert_called_once_with("paper_1", payload)

    def test_promotion_survives_an_unreachable_graph(self, db_session, make_draft, make_user):
        """A curator's review must not be lost because Neo4j was restarting."""
        repo = MagicMock()
        repo.store_curated_entity.side_effect = RuntimeError("connection refused")
        draft = make_draft()
        curation.promote_draft(db_session, draft.id, make_user(), graph_repository=repo)
        assert db_session.get(ExtractionDraft, draft.id).status == DraftStatus.PROMOTED.value

    def test_a_deferred_graph_write_is_replayable(self, db_session, make_draft, make_user):
        repo = MagicMock()
        repo.store_curated_entity.side_effect = RuntimeError("connection refused")
        draft = make_draft()
        curation.promote_draft(db_session, draft.id, make_user(), graph_repository=repo)
        assert [d.id for d in curation.pending_graph_writes(db_session)] == [draft.id]

    def test_a_successful_write_is_not_replayable(self, db_session, make_draft, make_user):
        draft = make_draft()
        curation.promote_draft(db_session, draft.id, make_user(), graph_repository=MagicMock())
        assert curation.pending_graph_writes(db_session) == []

    def test_promote_is_idempotent(self, db_session, make_draft, make_user):
        repo = MagicMock()
        draft = make_draft()
        user = make_user()
        curation.promote_draft(db_session, draft.id, user, graph_repository=repo)
        curation.promote_draft(db_session, draft.id, user, graph_repository=repo)
        assert repo.store_curated_entity.call_count == 1

    def test_cannot_promote_a_rejected_draft(self, db_session, make_draft, make_user):
        draft = make_draft()
        user = make_user()
        curation.reject_draft(db_session, draft.id, user)
        with pytest.raises(CurationError, match="rejected"):
            curation.promote_draft(db_session, draft.id, user)

    def test_cannot_reject_a_promoted_draft(self, db_session, make_draft, make_user):
        draft = make_draft()
        user = make_user()
        curation.promote_draft(db_session, draft.id, user)
        with pytest.raises(CurationError, match="promoted"):
            curation.reject_draft(db_session, draft.id, user)

    def test_resolution_is_audited_with_the_actor(self, db_session, make_draft, make_user):
        draft = make_draft()
        user = make_user()
        curation.promote_draft(db_session, draft.id, user, reason="manual")
        row = db_session.query(AuditLog).filter(AuditLog.action == "draft.promoted").one()
        assert row.actor_id == user.id
        assert row.target_id == draft.id


class TestReputation:
    def test_agreeing_with_the_outcome_earns_reputation(self, db_session, make_draft, make_user):
        draft = make_draft()
        agreeing = make_user("a@x.com")
        curation.attest(db_session, draft.id, agreeing, 1, auto_promote=False)
        curation.promote_draft(db_session, draft.id, None)
        assert agreeing.reputation == 1

    def test_disagreeing_costs_nothing(self, db_session, make_draft, make_user):
        # Penalising a losing vote would teach curators to vote with the
        # crowd, which defeats the point of attestation.
        draft = make_draft()
        dissenter = make_user("b@x.com")
        curation.attest(db_session, draft.id, dissenter, -1, auto_promote=False)
        curation.promote_draft(db_session, draft.id, None)
        assert dissenter.reputation == 0

    def test_leaderboard_ranks_by_reputation(self, db_session, make_draft, make_user):
        low, high = make_user("low@x.com"), make_user("high@x.com")
        low.reputation, high.reputation = 1, 9
        db_session.commit()
        board = curation.leaderboard(db_session)
        assert [c["user_id"] for c in board] == [high.id, low.id]

    def test_leaderboard_omits_users_with_no_reputation(self, db_session, make_user):
        make_user("nobody@x.com")
        assert curation.leaderboard(db_session) == []
