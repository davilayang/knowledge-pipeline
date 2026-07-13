"""Cascade engine: runs handler tiers in order until a result meets the quality floor."""

import logging
import time

from fetcher.metadata import ATTRIBUTION_KEYS
from fetcher.rate_limits import get_semaphore
from fetcher.types import (
    CascadeResult,
    FetchContext,
    RawTierResult,
    Tier,
    TierLogEntry,
    URLHandler,
)


logger = logging.getLogger(__name__)


def _floor_for_quality(tier: Tier, quality: str) -> int:
    return tier.min_chars if quality == "fast" else tier.high_chars


def _tier_meets_floor(tier: Tier, content: str, quality: str) -> bool:
    if len(content) < _floor_for_quality(tier, quality):
        return False
    return tier.validate is None or tier.validate(content)


async def _run_tier(handler: URLHandler, ctx: FetchContext, tier: Tier, url: str) -> RawTierResult:
    key = tier.rate_limit_key or handler.NAME
    semaphore = get_semaphore(key)
    async with semaphore:
        return await tier.run(ctx, url)


def _classify_outcome(
    tier: Tier, raw: RawTierResult, *, validated: bool, quality: str
) -> tuple[str, str | None]:
    """Return (error_kind, error_sentinel) for a tier that returned a RawTierResult.

    error_kind is the categorical reason (one of: ok, http_error, empty,
    validation_failed, below_floor). error_sentinel keeps the original
    "empty" / None values that older log readers expect."""
    if _tier_meets_floor(tier, raw.content, quality):
        return "ok", None
    if not raw.content:
        if raw.status and raw.status >= 400:
            return "http_error", "empty"
        return "empty", "empty"
    if not validated:
        return "validation_failed", None
    return "below_floor", None


def _exception_entry(
    tier: Tier, exc: BaseException, *, quality: str, duration_ms: int
) -> TierLogEntry:
    return TierLogEntry(
        tier=tier.name,
        status=0,
        chars=0,
        error=str(exc),
        validated=False,
        duration_ms=duration_ms,
        floor=_floor_for_quality(tier, quality),
        error_kind="exception",
        detail=f"{type(exc).__name__}: {exc}"[:500],
    )


def _result_entry(
    tier: Tier, raw: RawTierResult, *, validated: bool, quality: str, duration_ms: int
) -> TierLogEntry:
    error_kind, error_sentinel = _classify_outcome(tier, raw, validated=validated, quality=quality)
    detail = raw.detail
    if detail is None and error_kind == "below_floor":
        detail = f"got {len(raw.content)} chars, floor {_floor_for_quality(tier, quality)}"
    return TierLogEntry(
        tier=tier.name,
        status=raw.status,
        chars=len(raw.content),
        error=error_sentinel,
        validated=validated,
        duration_ms=duration_ms,
        floor=_floor_for_quality(tier, quality),
        error_kind=error_kind,
        detail=detail,
    )


async def run_cascade(
    handler: URLHandler,
    ctx: FetchContext,
    url: str,
    *,
    quality: str,
    allow_paid: bool,
) -> CascadeResult:
    """Walk handler.TIERS in declared preference order until one clears the floor.

    Order is the handler's — a quality-first handler (arXiv: llamaparse then
    pymupdf) is honoured, not forced free-before-paid. `cost` is an orthogonal
    spend axis: a `paid` tier is skipped when the caller passes allow_paid=False,
    wherever it sits in the order.
    """
    tier_log: list[TierLogEntry] = []
    best_result: tuple[Tier, RawTierResult] | None = None
    # Attribution metadata (title / authors / published) accumulated from every
    # validated tier that did NOT win, so a field one tier fetched survives even
    # when another tier's content wins — e.g. paywalled Medium: Jina's preamble
    # carries the published date but its body falls below floor, so RapidAPI wins
    # the body with no date. Only attribution keys are carried (not per-tier junk
    # like YouTube's `chunks`); the winning tier's own metadata always takes
    # precedence, earlier tiers only fill the attribution gaps it leaves.
    carried_attr: dict = {}

    def _carry(raw: RawTierResult) -> None:
        carried_attr.update({k: v for k, v in raw.metadata.items() if v and k in ATTRIBUTION_KEYS})

    def _final_meta(winning: RawTierResult) -> dict:
        final = dict(winning.metadata)
        for key, value in carried_attr.items():
            if not final.get(key):
                final[key] = value
        return final

    for tier in handler.TIERS:
        if tier.cost == "paid" and not allow_paid:
            continue
        if tier.applies is not None and not tier.applies(url):
            continue
        started = time.monotonic()
        try:
            raw = await _run_tier(handler, ctx, tier, url)
        except Exception as exc:
            tier_log.append(
                _exception_entry(
                    tier, exc, quality=quality, duration_ms=int((time.monotonic() - started) * 1000)
                )
            )
            if tier.cost == "paid" and handler.STRICT_PAID_TIER:
                raise
            continue
        duration_ms = int((time.monotonic() - started) * 1000)
        validated = tier.validate is None or tier.validate(raw.content)
        tier_log.append(
            _result_entry(tier, raw, validated=validated, quality=quality, duration_ms=duration_ms)
        )
        tier_log.extend(raw.extra_tier_log)
        if _tier_meets_floor(tier, raw.content, quality):
            return CascadeResult(raw.content, tier.name, tier_log, metadata=_final_meta(raw))
        # Carry attribution metadata onto the eventual winner from a validated
        # tier, or from a rejected tier that opted in via carry_meta_on_reject.
        # Content is separate: only a validated tier competes for best_result, so
        # invalid content never wins — only trusted metadata does.
        if validated or tier.carry_meta_on_reject:
            _carry(raw)
        if validated and (best_result is None or len(raw.content) > len(best_result[1].content)):
            best_result = (tier, raw)

    if best_result is not None:
        return CascadeResult(
            best_result[1].content,
            best_result[0].name,
            tier_log,
            metadata=_final_meta(best_result[1]),
        )
    return CascadeResult("", "", tier_log, metadata={})
