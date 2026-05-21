from typing import Literal

from pydantic import BaseModel, Field

CheckType = Literal["law", "str", "standard", "document"]


class CheckItem(BaseModel):
    """Single compliance check unit. Matches the TypeScript CheckItem in nextjs-fe/lib/compliance-config/loader.ts."""

    code: str
    title: str
    category: str
    check_type: CheckType
    requirement_text: str | None = None
    keywords: list[str] = Field(default_factory=list)


class ComplianceResult(BaseModel):
    """Single compliance verdict. Matches the FE ComplianceResult shape (snake_case for API/DB)."""

    str_code: str
    check_type: CheckType
    status: Literal["pass", "partial", "fail"]
    comment: str
    confidence: float
    is_error: bool = False
    source_pages: list[int] = Field(default_factory=list)
    citation: str | None = None
    """Concrete article reference inside the regulation, e.g. 'STR 2.02.01:2004, 4.3 str.'.

    Populated by RAGChecker when retrieved excerpts carry article metadata. ``None`` for
    BasicChecker (no retrieval → no article info available — asking the LLM to cite would
    hallucinate). Consumed by the legacy Excel reporter ('Šaltinis' column) and surfaced to
    the FE so it can render a citation badge alongside source_pages.
    """
