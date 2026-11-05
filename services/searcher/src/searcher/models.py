"""Request and response shapes of `POST /v1/search`, field for field as the hub's
`contracts/search.md` §1 (request) and §2 (response) define them.

Field order is the contract's arrival order. A field added here without a §2
entry is a contract breach: `tests/test_search.py` pins the field lists.
"""

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = 1

Caller = Literal["voice", "mcp"]
# `corpus`: one saved item in corpus.db, whatever its medium (article, talk, podcast, paper).
Source = Literal["corpus", "sessions", "notes", "briefs", "wiki"]
Kind = Literal["corpus", "session", "note", "brief", "wiki"]
Status = Literal["ok", "nothing_matched", "partial", "unavailable"]
Outcome = Literal["ok", "failed", "timed_out", "skipped"]

SOURCES: tuple[Source, ...] = ("corpus", "sessions", "notes", "briefs", "wiki")


class SearchRequest(BaseModel):
    # Unknown fields are rejected and nothing is coerced (no "5" for 5, no
    # datetime for a date), so a misspelt or mistyped filter never widens a
    # search.
    model_config = ConfigDict(extra="forbid", strict=True)

    # An int, never a bool (a Literal[1] would take `true`, since True == 1),
    # and only the version this service serves.
    contract_version: int = Field(ge=CONTRACT_VERSION, le=CONTRACT_VERSION)
    query: str
    caller: Caller
    # Omitted means the caller's default. The None default is never validated,
    # so an explicit null is rejected rather than read as omitted; `since` alone
    # takes null on the wire. An empty `sources` is rejected, never read as all.
    k: int = Field(default=None, ge=1, le=50)
    sources: list[Source] = Field(default=None, min_length=1)
    since: date | None = None

    @field_validator("since", mode="before")
    @classmethod
    def _iso_date(cls, v: object) -> object:
        # Strict mode takes only `date` objects, and json carries an ISO string,
        # so exactly YYYY-MM-DD is parsed here; anything else is still refused.
        if isinstance(v, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
            return date.fromisoformat(v)
        return v


class Lane(BaseModel):
    outcome: Outcome
    candidates: int
    returned: int


class MatchedBy(BaseModel):
    query: str
    lane: Source
    method: str
    rank: int
    score: float


class Item(BaseModel):
    uri: str
    kind: Kind
    title: str
    author: str | None
    date: date | None
    url: str | None
    summary: str | None
    passages: list[str]
    meta: dict[str, Any]
    matched_by: list[MatchedBy]


class Trace(BaseModel):
    stages_ms: dict[str, float] = {}
    code_rev: str | None = None
    index_version: str | None = None


class SearchResponse(BaseModel):
    contract_version: int = CONTRACT_VERSION
    status: Status
    lanes: dict[Source, Lane]
    items: list[Item]
    trace: Trace = Trace()
