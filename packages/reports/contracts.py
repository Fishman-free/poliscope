from __future__ import annotations

from uuid import UUID

from packages.kernel.contracts import ContractModel


class ReportRequest(ContractModel):
    task_id: UUID
    format: str = "markdown"  # markdown or json


class ReportResponse(ContractModel):
    task_id: UUID
    content: str
    format: str
    safety_notice_included: bool = False
