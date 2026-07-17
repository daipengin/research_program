"""Second-generation oscillator simulation engine.

The package is intentionally independent from ``research_program.simulation``
so that new oscillator algorithms can be developed without changing the
current research simulator.
"""

from research_program.simulation2.config import Simulation2Config
from research_program.simulation2.scheduler import EventScheduler

__all__ = ["EventScheduler", "Simulation2Config"]
