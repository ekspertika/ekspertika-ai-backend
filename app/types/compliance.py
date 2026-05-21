from typing import TypedDict


class PageDict(TypedDict):
    page_number: int
    text: str
    char_count: int


class NormativeExtractionResult(TypedDict):
    str_codes: list[str]
    laws: list[str]
    hn_norms: list[str]
    other_normatives: list[str]
