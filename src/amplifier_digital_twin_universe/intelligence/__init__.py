"""The model seam. Import the protocol, not an implementation."""

from amplifier_digital_twin_universe.intelligence.interface import Intelligence, default_intelligence, resolve
from amplifier_digital_twin_universe.intelligence.schemas import ModelRequest, ModelResult

__all__ = [
    "Intelligence",
    "ModelRequest",
    "ModelResult",
    "default_intelligence",
    "resolve",
]
