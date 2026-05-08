"""Minion agents package."""
from .pool import MinionPool

# Lazy imports — these pull in core dependencies only when used
# from .base import MinionAgent, MinionResult
# from .claude import MinionClaude
# from .perplexity import MinionPerplexity
# from .kimi import MinionKimi

__all__ = ["MinionPool"]
