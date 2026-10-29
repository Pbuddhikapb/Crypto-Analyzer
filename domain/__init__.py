"""
Shared Domain Contracts and Data Models.

Defines cross-cutting data contracts shared across Data, Analysis, and Simulation:
- CandidateSignal: Standardized candidate signal contract emitted on bar i close for execution on bar i+1 open.
- ExitConfig: Single Exit Authority configuration parameters.
"""

from domain.signals import CandidateSignal
from domain.exits import ExitConfig

__all__ = [
    "CandidateSignal",
    "ExitConfig",
]
