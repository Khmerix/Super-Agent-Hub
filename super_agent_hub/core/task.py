"""
Task models shared across the hub.
"""
from enum import Enum, auto


class TaskPriority(Enum):
    CRITICAL = auto()
    HIGH = auto()
    NORMAL = auto()
    LOW = auto()
