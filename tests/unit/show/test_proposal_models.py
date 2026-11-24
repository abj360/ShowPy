#!/usr/bin/env python3

"""
test_proposal_models.py --- tests the proposal states and the accepted revision

Contains:
    test_proposal_states_are_open_accepted_rejected_withdrawn: lists the four states
    test_proposal_accepted_revision_defaults_to_empty_and_round_trips: keeps the revision
"""

from e8s.show.collab_models import Proposal, ProposalState


def test_proposal_states_are_open_accepted_rejected_withdrawn() -> None:
    assert ProposalState.__value__.__args__ == ("open", "accepted", "rejected", "withdrawn")


def test_proposal_accepted_revision_defaults_to_empty_and_round_trips() -> None:
    assert Proposal(id="p", base_revision="r", change=()).accepted_revision == ""
    accepted = Proposal(
        id="p", base_revision="r", change=(), state="accepted", accepted_revision="r2"
    )
    assert Proposal.from_json(accepted.to_json()) == accepted
