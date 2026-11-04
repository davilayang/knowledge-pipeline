"""Credit search hits against the gold on a frozen snapshot.

A hit credits the needs of its probe whose uris it reaches:
  article -> that article
  session -> that session, and every article fetched in it (generous: a roundup
             session credits every gold piece fetched in it)
  note    -> the articles its chunk links to (or, with no link, the note file of
             that day whose text holds the chunk's heading)
  brief   -> the articles listed in the brief's `content_ids`
  wiki    -> nothing
Recall counts grade-2 needs found; precision counts items crediting any need.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from evals.search.dataset import GoldNeed

_URL_RX = re.compile(r"https?://[^\s)>\]|]+")


def article_uri(content_id: str) -> str:
    return f"kos://piece/article/{quote(content_id, safe=':')}"


def session_uri(session_id: str) -> str:
    return f"kos://piece/session/{quote(session_id, safe=':')}"


def _norm(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


def _frontmatter(text: str) -> dict:
    return (yaml.safe_load(text.split("---", 2)[1]) or {}) if text.startswith("---") else {}


SNAPSHOT_LANES = {
    "articles": "corpus.db",
    "sessions": "sessions.db",
    "notes": "notes_x",
    "briefs": "briefs_x",
    "wiki": "wiki_x",
}


def snapshot_digests(root: Path) -> dict[str, str]:
    """sha256 per lane over file paths and bytes, so changed text under an
    unchanged id changes the digest."""
    out = {}
    for lane, rel in SNAPSHOT_LANES.items():
        base = root / rel
        files = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        h = hashlib.sha256()
        for f in files:
            h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
        out[lane] = h.hexdigest()
    return out


@dataclass(frozen=True)
class Hit:
    kind: str
    key: str = ""
    chunk: str = ""
    day: str = ""


class Snapshot:
    """The frozen corpus a run is scored on: `corpus.db`, `sessions.db`, and the
    note and brief files, read-only."""

    def __init__(self, root: Path) -> None:
        self.root = root
        corpus = self._ro("corpus.db")
        sessions = self._ro("sessions.db")
        self.content_ids = {r[0] for r in corpus.execute("select content_id from contents")}
        self.session_ids = {r[0] for r in sessions.execute("select session_id from sessions")}
        self.url_to_cids: dict[str, set[str]] = {}
        for cid, url in corpus.execute("select content_id, url from contents"):
            for u in (url or "", cid.split("::", 1)[-1]):
                if u.startswith("http"):
                    self.url_to_cids.setdefault(_norm(u), set()).add(cid)
        self.session_pieces: dict[str, set[str]] = {}
        for sid, cid in sessions.execute(
            "select session_id, content_id from tool_calls"
            " where content_id is not null and tool_name like 'fetch%'"
        ):
            self.session_pieces.setdefault(sid, set()).add(cid)
        self.fetched_ids = set().union(*self.session_pieces.values())
        self.notes_by_day: dict[str, list[tuple[str, str]]] = {}
        for f in (root / "notes_x/notes").glob("*.md"):
            text = f.read_text()
            fm = _frontmatter(text)
            self.notes_by_day.setdefault(str(fm.get("date", "")), []).append(
                (text, fm.get("url") or "")
            )
        self.brief_cids: dict[str, set[str]] = {}
        for f in (root / "briefs_x/briefs").glob("*.md"):
            cids = set(_frontmatter(f.read_text()).get("content_ids") or [])
            self.brief_cids[f.name] = cids
            self.brief_cids[f.name.split("_", 1)[-1]] = cids

    def _ro(self, name: str) -> sqlite3.Connection:
        path = self.root / name
        if not path.exists():
            raise FileNotFoundError(f"snapshot is missing {path}")
        return sqlite3.connect(f"file:{path}?immutable=1", uri=True)

    def uris_reached(self, hit: Hit) -> set[str]:
        if hit.kind == "article":
            return {article_uri(hit.key)}
        if hit.kind == "session":
            pieces = self.session_pieces.get(hit.key, set())
            return {session_uri(hit.key)} | {article_uri(c) for c in pieces}
        if hit.kind == "note":
            return {article_uri(c) for c in self._note_cids(hit.chunk, hit.day)}
        if hit.kind == "brief":
            cids = self.brief_cids.get(hit.key.removeprefix("local:"), set())
            return {article_uri(c) for c in cids}
        return set()

    def _note_cids(self, chunk: str, day: str) -> set[str]:
        urls = _URL_RX.findall(chunk)
        if not urls:
            head = next(
                (ln.strip("# ").strip() for ln in chunk.splitlines() if ln.startswith("#")), ""
            )
            urls = [u for text, u in self.notes_by_day.get(day, []) if head and head in text and u]
        out: set[str] = set()
        for u in urls:
            out |= self.url_to_cids.get(_norm(u), {u})
        return out


@dataclass(frozen=True)
class ViewScore:
    probes: int
    items: int
    relevant_items: int
    found: int
    total: int
    macro_recall: float
    primary_found: int
    primary_total: int

    @property
    def recall(self) -> float:
        return self.found / self.total

    @property
    def precision(self) -> float:
        return self.relevant_items / self.items

    @property
    def f2(self) -> float:
        p, r = self.precision, self.recall
        return 5 * p * r / (4 * p + r) if p + r else 0.0


def score_view(
    hits: dict[int, list[Hit]], gold: list[GoldNeed], snapshot: Snapshot, *, negatives: set[int]
) -> ViewScore:
    """Score one view (one caller's item list per probe). Negative probes are skipped."""
    by_probe: dict[int, list[GoldNeed]] = {}
    for g in gold:
        by_probe.setdefault(g.probe_id, []).append(g)
    found = total = items = relevant = prim_found = prim_total = 0
    per_probe: list[float] = []
    for pid, probe_hits in hits.items():
        if pid in negatives:
            continue
        needs = by_probe.get(pid, [])
        credited: set[str] = set()
        for h in probe_hits:
            reached = snapshot.uris_reached(h)
            ids = {g.gold_id for g in needs if reached & set(g.uris)}
            relevant += bool(ids)
            credited |= ids
        items += len(probe_hits)
        main = {g.gold_id for g in needs if g.grade == 2}
        got = credited & main
        found, total = found + len(got), total + len(main)
        per_probe.append(len(got) / len(main) if main else 0.0)
        primary = {g.gold_id for g in needs if g.primary}
        if primary:
            prim_total += 1
            prim_found += bool(credited & primary)
    return ViewScore(
        probes=len(per_probe),
        items=items,
        relevant_items=relevant,
        found=found,
        total=total,
        macro_recall=sum(per_probe) / len(per_probe) if per_probe else 0.0,
        primary_found=prim_found,
        primary_total=prim_total,
    )
