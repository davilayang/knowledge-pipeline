"""YouTube upload date from the watch page's SEO microformat.

oEmbed carries no date and the player/InnerTube API is heavily anti-bot gated
(and needs yt-dlp's churn). But YouTube server-renders `"uploadDate"` into the
watch page's SEO microformat — a stable, crawler-facing field readable with a
plain GET + regex, no API key, no JS.

Data-center IPs get a consent-wall variant that strips the field, so the fetch
routes through the same SOCKS5 residential proxy the transcript tier uses, via
curl_cffi (browser impersonation + SOCKS support, already a dependency). Fetch is
best-effort: any failure yields None so the date is left absent, never invented.
"""

import logging
import re

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

_UPLOAD_DATE_RE = re.compile(r'"uploadDate":"([^"]+)"')


def parse_upload_date(html: str) -> str | None:
    """The `uploadDate` value from watch-page HTML, or None if absent."""
    match = _UPLOAD_DATE_RE.search(html or "")
    return match.group(1) if match else None


async def fetch_upload_date(socks5_url: str, video_url: str, *, timeout: int) -> str | None:
    """Best-effort upload date for a YouTube video — never raises."""
    proxies = {"https": socks5_url, "http": socks5_url} if socks5_url else None
    try:
        async with AsyncSession(impersonate="safari17_0") as session:
            resp = await session.get(video_url, proxies=proxies, timeout=timeout)
        if resp.status_code != 200:
            return None
        return parse_upload_date(resp.text or "")
    except Exception as exc:
        logger.info("youtube upload-date fetch failed for %s: %s", video_url, exc)
        return None
