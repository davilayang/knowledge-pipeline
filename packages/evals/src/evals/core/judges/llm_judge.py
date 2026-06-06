"""Per-field LLM-as-judge with injected chat_fn.

`chat_fn` is `Callable[[str], dict[str, float]]` — caller is responsible for
prompt assembly + JSON parsing on the chat side. Production wires in a thin
wrapper around `workflows.llm.generate_structured_with_usage(schema=...)`
(later step). Tests pass a closure returning a fixed dict.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from evals.core.types import FieldScore


@dataclass(frozen=True)
class LLMJudge:
    fields: Sequence[str]
    chat_fn: Callable[[str], dict]
    prompt_template: str

    def score(self, *, expected: dict, actual: dict) -> FieldScore:
        prompt = self.prompt_template.format(expected=expected, actual=actual, fields=self.fields)
        raw = self.chat_fn(prompt)
        values = {f: float(raw.get(f, 0.0)) for f in self.fields}
        return FieldScore(value=values, metadata={"judge_name": "llm", "raw": raw})
