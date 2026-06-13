from . import floor, zero_shot
from .base import Decision
from .floor import FloorAgent
from .heuristic import RandomAgent, ScriptedAgent
from .zero_shot import ZeroShotAgent

# LLM context scaffolds, by the name runs are recorded and compared under;
# each entry pairs the agent class with the system prompt it plays under.
SCAFFOLDS: dict[str, tuple[type, str]] = {
    "floor": (FloorAgent, floor.SYSTEM_PROMPT),  # one conversation per floor (default)
    "stepwise": (ZeroShotAgent, zero_shot.SYSTEM_PROMPT),  # stateless ablation baseline
}

__all__ = [
    "Decision",
    "FloorAgent",
    "RandomAgent",
    "SCAFFOLDS",
    "ScriptedAgent",
    "ZeroShotAgent",
]
