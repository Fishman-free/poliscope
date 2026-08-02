"""``check_diversity`` flags a task's acquired sources when none are independent.

CLAUDE.md 7.4 and design spec 7.9: several sources that share one dataset or
one research team are one piece of evidence, not several corroborating ones.
"""

from __future__ import annotations

from uuid import uuid4

from packages.evidence.source_diversity import SourceDiversityInput, check_diversity


def test_single_source_cannot_demonstrate_a_lack_of_diversity() -> None:
    assert check_diversity([SourceDiversityInput(source_id=uuid4())]) is None


def test_no_sources_returns_none() -> None:
    assert check_diversity([]) is None


def test_shared_dataset_id_triggers_a_finding() -> None:
    s1, s2 = uuid4(), uuid4()
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=s1, dataset_id="add-health"),
            SourceDiversityInput(source_id=s2, dataset_id="add-health"),
        ]
    )
    assert finding is not None
    assert "add-health" in finding.reason
    assert set(finding.source_ids) == {s1, s2}


def test_different_dataset_ids_do_not_trigger() -> None:
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=uuid4(), dataset_id="add-health"),
            SourceDiversityInput(source_id=uuid4(), dataset_id="other-cohort"),
        ]
    )
    assert finding is None


def test_missing_dataset_ids_never_trigger_on_their_own() -> None:
    """Two rows both lacking a dataset_id are not thereby "the same" dataset."""
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=uuid4()),
            SourceDiversityInput(source_id=uuid4()),
        ]
    )
    assert finding is None


def test_shared_author_triggers_a_finding_when_no_dataset_signal() -> None:
    s1, s2 = uuid4(), uuid4()
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=s1, authors=("Jane Doe", "Alex Lee")),
            SourceDiversityInput(source_id=s2, authors=("jane doe",)),
        ]
    )
    assert finding is not None
    assert "jane doe" in finding.reason
    assert set(finding.source_ids) == {s1, s2}


def test_one_source_with_no_authors_blocks_the_author_signal() -> None:
    """A source with no author data cannot demonstrate a shared team either."""
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=uuid4(), authors=("Jane Doe",)),
            SourceDiversityInput(source_id=uuid4(), authors=()),
        ]
    )
    assert finding is None


def test_disjoint_author_sets_do_not_trigger() -> None:
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=uuid4(), authors=("Jane Doe",)),
            SourceDiversityInput(source_id=uuid4(), authors=("Alex Lee",)),
        ]
    )
    assert finding is None


def test_dataset_signal_is_checked_before_author_signal() -> None:
    """Both signals hold; the dataset reason is the one surfaced."""
    s1, s2 = uuid4(), uuid4()
    finding = check_diversity(
        [
            SourceDiversityInput(
                source_id=s1, dataset_id="add-health", authors=("Jane Doe",)
            ),
            SourceDiversityInput(
                source_id=s2, dataset_id="add-health", authors=("Jane Doe",)
            ),
        ]
    )
    assert finding is not None
    assert "add-health" in finding.reason
    assert "数据集" in finding.reason


def test_three_sources_need_all_three_to_share_an_author() -> None:
    s1, s2, s3 = uuid4(), uuid4(), uuid4()
    finding = check_diversity(
        [
            SourceDiversityInput(source_id=s1, authors=("Jane Doe", "Alex Lee")),
            SourceDiversityInput(source_id=s2, authors=("Jane Doe",)),
            SourceDiversityInput(source_id=s3, authors=("Someone Else",)),
        ]
    )
    assert finding is None
