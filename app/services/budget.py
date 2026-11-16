import time
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class Budget:
    deadline: float
    searches: int = 0
    fetches: int = 0
    llm_calls: int = 0
    tokens: int = 0
    degraded: bool = False
    reasons: list[str] = field(default_factory=list)
    last_error: Exception | None = None   # most recent LLM/embedding failure (for honest error reporting)

    def _hit(self, why: str) -> None:
        self.degraded = True
        if why not in self.reasons:
            self.reasons.append(why)

    def record_error(self, err: Exception) -> None:
        self.last_error = err
        self._hit("a service error occurred")

    def time_up(self) -> bool:
        if time.monotonic() >= self.deadline:
            self._hit("time limit")
            return True
        return False

    def exhausted(self) -> bool:
        """True when we should stop new work and wrap up with what we have."""
        if self.time_up():
            return True
        if self.llm_calls >= settings.budget_max_llm_calls:
            self._hit("model-call limit")
            return True
        return False

    def take_search(self) -> bool:
        if self.searches >= settings.budget_max_searches:
            self._hit("search limit")
            return False
        self.searches += 1
        return True

    def take_fetch(self) -> bool:
        if self.fetches >= settings.budget_max_fetches:
            self._hit("page-fetch limit")
            return False
        self.fetches += 1
        return True

    def summary(self) -> str:
        return (f"searches={self.searches} fetches={self.fetches} llm_calls={self.llm_calls} "
                f"tokens={self.tokens} degraded={self.degraded} reasons={self.reasons}")


_budgets: dict[str, Budget] = {}


def get_budget(job_id: str) -> Budget:
    if job_id not in _budgets:
        _budgets[job_id] = Budget(deadline=time.monotonic() + settings.budget_max_seconds)
    return _budgets[job_id]


def drop_budget(job_id: str) -> None:
    _budgets.pop(job_id, None)
