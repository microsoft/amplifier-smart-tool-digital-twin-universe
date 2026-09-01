"""What crosses the model seam.

These types are provider-neutral on purpose. Nothing here names a vendor, an SDK,
or a wire format, so an implementation can be replaced without touching a caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ModelRequest:
    """One prompt, and how the caller wants it answered."""

    prompt: str
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ModelResult:
    """One completed turn: the reply, and what it cost."""

    text: str
    provider: str = ""
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal | None = None

    def usage(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": None if self.cost_usd is None else str(self.cost_usd),
        }
