"""Canonical URL → content-type classification, shared by kp's triage
(`classify_content_type`) and the fetcher's routing intent.

One source so the two layers can't drift on what a URL *is*. arXiv identity lives
in the sibling `arxiv_urls` module (regex-based); this module owns the host-set and
file-suffix rules for the rest of the taxonomy.

Returns the lowercase taxonomy: youtube / arxiv / medium / facebook / github /
file_pdf / file_audio / article (the catch-all). Precedence follows the fetcher's
registry order — host-matched platforms first, then file-suffix, then article.
"""

from urllib.parse import urlparse

from domains.arxiv_urls import is_arxiv_url

_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
)
_FACEBOOK_BARE_HOSTS = frozenset({"facebook.com", "fb.com", "fb.watch"})
# file_audio names the class "an audio/av file", not one extension — whisper
# handles them all, and a zencastr .mp4 podcast already exists in the corpus.
_AUDIO_SUFFIXES = (".mp3", ".m4a", ".ogg", ".wav", ".opus", ".mp4")


def classify_url_type(url: str) -> str:
    """Pure URL → content-type. Never raises — malformed input falls to `article`.

    `medium` is not yet emitted here (its domain set still lives in the fetcher
    package); Medium URLs fall through to `article` until that set moves into
    `domains`. Every other type is authoritative.
    """
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
    if bare == "github.com" or bare.endswith(".github.com"):
        return "github"
    if bare in _FACEBOOK_BARE_HOSTS or bare.endswith(".facebook.com"):
        return "facebook"
    if path.endswith(".pdf"):
        return "file_pdf"
    if path.endswith(_AUDIO_SUFFIXES):
        return "file_audio"
    return "article"
