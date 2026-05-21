from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class STRCheckResult:
    code: str
    full_name: str
    category: str
    present_in_doc: bool
    status: Literal["pass", "fail", "partial", "not_applicable", "error"]
    comment: str
    page_references: list[int] = field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    error_message: str | None = None
    citation: str | None = None


@dataclass
class DocumentCheckResult:
    document_name: str
    found: bool
    comment: str


@dataclass
class ComplianceReport:
    filename: str
    checked_at: datetime
    total_pages: int
    str_results: list[STRCheckResult]
    document_results: list[DocumentCheckResult]
    warnings: list[str] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.str_results if r.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.str_results if r.status == "fail")

    @property
    def partial_count(self) -> int:
        return sum(1 for r in self.str_results if r.status == "partial")

    @property
    def docs_found_count(self) -> int:
        return sum(1 for r in self.document_results if r.found)
