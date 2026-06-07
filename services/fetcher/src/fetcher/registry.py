"""Source registry and tier cascade engine."""

from dataclasses import dataclass

from fetcher.rate_limits import get_semaphore
from fetcher.sources import article, arxiv, youtube
from fetcher.types import FetchContext, RawTierResult, Source, Tier, TierLogEntry


REGISTERED_SOURCES: list[Source] = [arxiv, youtube, article]  # type: ignore[list-item]


@dataclass(frozen=True)
class CascadeResult:
    content: str
    tier_used: str
    tier_log: list[TierLogEntry]


def find_source(url: str) -> Source | None:
    for source in REGISTERED_SOURCES:
        if source.matches(url):
            return source
    return None


def _tier_meets_floor(tier: Tier, content: str, quality: str) -> bool:
    floor = tier.min_chars if quality == "fast" else tier.high_chars
    if len(content) < floor:
        return False
    return tier.validate is None or tier.validate(content)


async def _run_tier(src: Source, ctx: FetchContext, tier: Tier, url: str) -> RawTierResult:
    semaphore = get_semaphore(src.NAME if tier.name != "jina" else "jina")
    async with semaphore:
        return await tier.run(ctx, url)


async def run_cascade(
    src: Source,
    ctx: FetchContext,
    url: str,
    *,
    quality: str,
    allow_paid: bool,
) -> CascadeResult:
    """Run free tiers first, then paid tiers when allowed."""
    tier_log: list[TierLogEntry] = []
    best_result: tuple[Tier, RawTierResult] | None = None

    for tier in src.TIERS:
        if tier.cost != "free":
            continue
        if tier.applies is not None and not tier.applies(url):
            continue
        try:
            raw = await _run_tier(src, ctx, tier, url)
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
            return CascadeResult(raw.content, tier.name, tier_log)
        if best_result is None or len(raw.content) > len(best_result[1].content):
            best_result = (tier, raw)

    if allow_paid:
        for tier in src.TIERS:
            if tier.cost != "paid":
                continue
            if tier.applies is not None and not tier.applies(url):
                continue
            try:
                raw = await _run_tier(src, ctx, tier, url)
            except Exception as exc:
                tier_log.append(TierLogEntry(tier.name, 0, 0, str(exc), False))
                if src.STRICT_PAID_TIER:
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
                return CascadeResult(raw.content, tier.name, tier_log)
            if best_result is None or len(raw.content) > len(best_result[1].content):
                best_result = (tier, raw)

    if best_result is not None:
        return CascadeResult(best_result[1].content, best_result[0].name, tier_log)
    return CascadeResult("", "", tier_log)
