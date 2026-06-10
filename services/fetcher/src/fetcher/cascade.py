"""Cascade engine: runs handler tiers in order until a result meets the quality floor."""

import logging

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


def _tier_meets_floor(tier: Tier, content: str, quality: str) -> bool:
    floor = tier.min_chars if quality == "fast" else tier.high_chars
    if len(content) < floor:
        return False
    return tier.validate is None or tier.validate(content)


async def _run_tier(handler: URLHandler, ctx: FetchContext, tier: Tier, url: str) -> RawTierResult:
    key = tier.rate_limit_key or handler.NAME
    semaphore = get_semaphore(key)
    async with semaphore:
        return await tier.run(ctx, url)


async def run_cascade(
    handler: URLHandler,
    ctx: FetchContext,
    url: str,
    *,
    quality: str,
    allow_paid: bool,
) -> CascadeResult:
    """Run free tiers first, then paid tiers when allowed."""
    tier_log: list[TierLogEntry] = []
    best_result: tuple[Tier, RawTierResult] | None = None

    for tier in handler.TIERS:
        if tier.cost != "free":
            continue
        if tier.applies is not None and not tier.applies(url):
            continue
        try:
            match tier:
                case Tier():
                    raw = await _run_tier(handler, ctx, tier, url)
                case _:
                    logger.warning("unknown tier kind: %s", type(tier))
                    continue
        except Exception as exc:
            tier_log.append(TierLogEntry(tier.name, 0, 0, str(exc), False))
            continue
        validated = tier.validate is None or tier.validate(raw.content)
        tier_log.append(
            TierLogEntry(
                tier.name, raw.status, len(raw.content), None if raw.content else "empty", validated
            )
        )
        if _tier_meets_floor(tier, raw.content, quality):
            return CascadeResult(raw.content, tier.name, tier_log, metadata=raw.metadata)
        if validated and (best_result is None or len(raw.content) > len(best_result[1].content)):
            best_result = (tier, raw)

    if allow_paid:
        for tier in handler.TIERS:
            if tier.cost != "paid":
                continue
            if tier.applies is not None and not tier.applies(url):
                continue
            try:
                match tier:
                    case Tier():
                        raw = await _run_tier(handler, ctx, tier, url)
                    case _:
                        logger.warning("unknown tier kind: %s", type(tier))
                        continue
            except Exception as exc:
                tier_log.append(TierLogEntry(tier.name, 0, 0, str(exc), False))
                if handler.STRICT_PAID_TIER:
                    raise
                continue
            validated = tier.validate is None or tier.validate(raw.content)
            tier_log.append(
                TierLogEntry(
                    tier.name,
                    raw.status,
                    len(raw.content),
                    None if raw.content else "empty",
                    validated,
                )
            )
            if _tier_meets_floor(tier, raw.content, quality):
                return CascadeResult(raw.content, tier.name, tier_log, metadata=raw.metadata)
            if validated and (
                best_result is None or len(raw.content) > len(best_result[1].content)
            ):
                best_result = (tier, raw)

    if best_result is not None:
        return CascadeResult(
            best_result[1].content, best_result[0].name, tier_log, metadata=best_result[1].metadata
        )
    return CascadeResult("", "", tier_log, metadata={})
