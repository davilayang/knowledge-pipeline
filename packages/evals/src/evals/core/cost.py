"""Cost budgeting + estimator protocol.

A budget gates a benchmark/workbench launch: the harness asks the estimator
for a USD figure, calls `budget.check_estimate(usd)`, and aborts before
spending money if the projection exceeds the cap. Concurrency is also
expressed here so a single CostBudget controls both spend and throughput.
"""

from dataclasses import dataclass
from typing import Protocol


class BudgetExceededError(RuntimeError):
    pass


class CostEstimatorProtocol(Protocol):
    """Returns the projected total USD for a benchmark/workbench run.

    Implementations are provider-specific (e.g., one per chat-model pricing
    table). Kept open-ended on kwargs so the harness can pass whatever
    bookkeeping it has at launch time.
    """

    def __call__(self, **kwargs: object) -> float: ...


@dataclass(frozen=True)
class CostBudget:
    max_concurrent_calls: int = 4
    max_cost_usd_per_run: float = 5.00

    def check_estimate(self, *, estimated_usd: float) -> None:
        if estimated_usd > self.max_cost_usd_per_run:
            raise BudgetExceededError(
                f"Estimated ${estimated_usd:.2f} exceeds budget "
                f"of ${self.max_cost_usd_per_run:.2f} per run."
            )
