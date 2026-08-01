from __future__ import annotations


class CLIClient:
    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self.base_url = base_url
