"""Canonical URL → content-type classification, shared by kp's triage
(`classify_content_type`) and the fetcher's routing intent.

One source so the two layers can't drift on what a URL *is*. Platform identity
lives in sibling modules (`arxiv_urls`, `medium_urls`); this module owns the
remaining host-set + file-suffix rules and the precedence order.

Returns the lowercase taxonomy: youtube / arxiv / medium / facebook / github /
file_pdf / file_audio / article (the catch-all). Precedence follows the fetcher's
registry order — host-matched platforms first, then file-suffix, then article.
"""

from urllib.parse import urlparse

from domains.arxiv_urls import is_arxiv_url
from domains.medium_urls import is_medium_url

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)
_FACEBOOK_BARE_HOSTS = frozenset({"facebook.com", "fb.com", "fb.watch"})
# file_audio names the class "an audio/av file", not one extension — whisper
# handles them all (incl. video, from which it extracts audio), and a zencastr
# .mp4 podcast already exists in the corpus. Shared with the fetcher's file_audio
# handler so the classifier and the fetch routing agree on the set.
AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav", ".opus", ".flac", ".mp4", ".webm", ".mov")


def classify_url_type(url: str) -> str:
    """Pure URL → content-type. Never raises — malformed input falls to `article`."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return "article"
    bare = host.removeprefix("www.")
    path = (parsed.path or "").lower()

    if host in _YOUTUBE_HOSTS:
        return "youtube"
    if is_arxiv_url(url):
        return "arxiv"
    if is_medium_url(url):
        return "medium"
    if bare == "github.com" or bare.endswith(".github.com"):
        return "github"
    if bare in _FACEBOOK_BARE_HOSTS or bare.endswith(".facebook.com"):
        return "facebook"
    if path.endswith(".pdf"):
        return "file_pdf"
    if path.endswith(AUDIO_SUFFIXES):
        return "file_audio"
    return "article"
