"""Public Grok exports.

`GrokImpl` is the concrete implementation.
`Grok` is kept as a compatibility alias for existing imports.
"""

from __future__ import annotations

from .grok_impl import GraphSanity, GrokImpl
from .interfaces import Grok as GrokProtocol

Grok = GrokImpl

__all__ = ["Grok", "GrokImpl", "GrokProtocol", "GraphSanity"]
