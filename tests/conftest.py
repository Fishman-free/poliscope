from typing import Any

import pytest

from tests.factories import make_research_contract


@pytest.fixture
def valid_research_contract() -> Any:
    """Return a deterministic valid ResearchContract instance."""
    return make_research_contract()
