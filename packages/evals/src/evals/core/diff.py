"""Per-field diff between two RunRecords + text/HTML renderers.

DiffReport walks each fixture in both records and records `(a_value, b_value)`
tuples per field. Fields are picked by the optional `field_picker` callable;
default takes all keys appearing in either side's output.
"""

import html
from collections.abc import Callable
from dataclasses import dataclass

from evals.core.types import RunRecord


@dataclass(frozen=True)
class DiffReport:
    variant_a_name: str
    variant_b_name: str
    per_fixture: dict  # fixture_id -> {field: (a_value, b_value)}


def _output_or_empty(sample) -> dict:
    return sample.output if sample.output is not None else {}


def diff_runs(
    a: RunRecord, b: RunRecord, *, field_picker: Callable[[str], bool] | None = None
) -> DiffReport:
    """Per-fixture per-field (a, b) tuples. Skips fixtures missing on either side."""
    a_by_id = {s.fixture_id: s for s in a.samples}
    b_by_id = {s.fixture_id: s for s in b.samples}
    common = sorted(set(a_by_id) & set(b_by_id))
    per_fixture: dict = {}
    for fid in common:
        a_out = _output_or_empty(a_by_id[fid])
        b_out = _output_or_empty(b_by_id[fid])
        fields = set(a_out) | set(b_out)
        if field_picker is not None:
            fields = {f for f in fields if field_picker(f)}
        per_fixture[fid] = {f: (a_out.get(f), b_out.get(f)) for f in sorted(fields)}
    return DiffReport(
        variant_a_name=a.variant_name, variant_b_name=b.variant_name, per_fixture=per_fixture
    )


def render_diff_text(report: DiffReport) -> str:
    lines = [f"=== {report.variant_a_name}  vs  {report.variant_b_name} ===", ""]
    for fid, fields in report.per_fixture.items():
        lines.append(f"# {fid}")
        for field_name, (av, bv) in fields.items():
            marker = " " if av == bv else "*"
            lines.append(f"  {marker} {field_name}: {av!r}  →  {bv!r}")
        lines.append("")
    return "\n".join(lines)


def render_diff_html(report: DiffReport) -> str:
    parts = [
        f"<h2>{html.escape(report.variant_a_name)} vs {html.escape(report.variant_b_name)}</h2>",
        "<table>",
    ]
    for fid, fields in report.per_fixture.items():
        parts.append(f"<tr><th colspan='3'>{html.escape(fid)}</th></tr>")
        for field_name, (av, bv) in fields.items():
            cls = "same" if av == bv else "diff"
            parts.append(
                f"<tr class='{cls}'>"
                f"<td>{html.escape(field_name)}</td>"
                f"<td>{html.escape(repr(av))}</td>"
                f"<td>{html.escape(repr(bv))}</td>"
                f"</tr>"
            )
    parts.append("</table>")
    return "\n".join(parts)
