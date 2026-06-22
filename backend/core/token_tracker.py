"""
Token Tracker — PostgreSQL persistence for per-execution token usage and cost tracking.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


@dataclass
class NodeTokenUsage:
    node_id: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_ms: int
    estimated_cost_usd: float = 0.0


# Cost per 1k tokens (input, output)
TOKEN_COSTS = {
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-3.5-turbo": (0.0005, 0.0015),
    "gemini-1.5-pro": (0.0035, 0.0105),
    "gemini-1.5-flash": (0.000075, 0.0003),
    "meta-llama/llama-3-70b-instruct": (0.0009, 0.0009),
    "llama3.1": (0.0, 0.0),
    "llama3.2": (0.0, 0.0),
    "unknown": (0.0, 0.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    cost = TOKEN_COSTS.get(model, TOKEN_COSTS["unknown"])
    return (cost[0] * input_tokens / 1000) + (cost[1] * output_tokens / 1000)


@dataclass
class ExecutionTokenBudget:
    exec_id: str
    max_total_tokens: int = field(
        default_factory=lambda: int(os.getenv("MAX_TOTAL_TOKENS", "100000"))
    )
    max_cost_usd: float = field(
        default_factory=lambda: float(os.getenv("MAX_COST_USD", "5.00"))
    )

    current_tokens: int = field(default=0)
    current_cost: float = field(default=0.0)
    usage_history: List[NodeTokenUsage] = field(default_factory=list)

    def record(self, usage: NodeTokenUsage):
        usage.estimated_cost_usd = estimate_cost(
            usage.model, usage.input_tokens, usage.output_tokens
        )
        self.current_tokens += usage.input_tokens + usage.output_tokens
        self.current_cost += usage.estimated_cost_usd
        self.usage_history.append(usage)

    def check_budget(self) -> Optional[str]:
        if self.current_tokens > self.max_total_tokens:
            return (
                f"BudgetExceededError: Token budget exceeded "
                f"({self.current_tokens} > {self.max_total_tokens})"
            )
        if self.current_cost > self.max_cost_usd:
            return (
                f"BudgetExceededError: Cost budget exceeded "
                f"(${self.current_cost:.3f} > ${self.max_cost_usd:.3f})"
            )
        return None

    async def persist(self):
        """Persist all usage records to PostgreSQL."""
        if not self.usage_history:
            return

        try:
            from core.database import AsyncSessionLocal
            from core.db_models import TokenUsageModel

            async with AsyncSessionLocal() as session:
                for usage in self.usage_history:
                    model = TokenUsageModel(
                        execution_id=self.exec_id,
                        node_id=usage.node_id,
                        model=usage.model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cost_usd=usage.estimated_cost_usd,
                        duration_ms=usage.duration_ms,
                    )
                    session.add(model)
                await session.commit()
                logger.debug(
                    f"Persisted {len(self.usage_history)} token usage records "
                    f"for execution {self.exec_id}"
                )
        except Exception as e:
            logger.error(f"Failed to persist token usage: {e}")

    def summary(self) -> dict:
        """Return a summary of token usage for this execution."""
        return {
            "exec_id": self.exec_id,
            "total_tokens": self.current_tokens,
            "total_cost_usd": round(self.current_cost, 6),
            "budget_tokens": self.max_total_tokens,
            "budget_cost_usd": self.max_cost_usd,
            "node_count": len(self.usage_history),
        }
