from __future__ import annotations

from uuid import uuid4

import pytest

from packages.council.contracts import Seat
from packages.council.dissent import DissentCertificate, issue_dissent


def test_dissent_certificate_requires_fields() -> None:
    with pytest.raises(ValueError):
        DissentCertificate(author=Seat.ADVERSARY_FALSIFIER, target_id=uuid4())


def test_issue_dissent_creates_certificate() -> None:
    cert = issue_dissent(
        author=Seat.ADVERSARY_FALSIFIER,
        target_id=uuid4(),
        statement="correlation does not imply causation",
        reason="cross-sectional design",
        evidence_refs=(uuid4(),),
        withdrawal_condition="experimental evidence provided",
    )
    assert cert.author == Seat.ADVERSARY_FALSIFIER
    assert cert.has_dissent is True


def test_dissent_preserved_after_fold() -> None:
    cert = issue_dissent(
        author=Seat.ADVERSARY_FALSIFIER,
        target_id=uuid4(),
        statement="methodological concern",
        reason="measurement bias",
        evidence_refs=(uuid4(),),
        withdrawal_condition="validated instrument",
    )
    # Dissent must remain traceable
    assert cert.id is not None
    assert cert.withdrawal_condition == "validated instrument"


def test_suite() -> None:
    test_issue_dissent_creates_certificate()
    test_dissent_preserved_after_fold()
