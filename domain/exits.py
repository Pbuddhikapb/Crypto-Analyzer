"""
Domain Contract: Trade Exit Configuration.

Parameter configuration for the Single Exit Authority (engine/exits.py).
Defines institutional bracket geometry and exit mode thresholds.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ExitConfig:
    """Configuration parameters for trade exit evaluation."""

    tp1_r: float = 0.75  # Target R for partial 50% scale-out
    be_arm_r: float = 0.85  # Excursion required to arm breakeven stop
    tp2_r: float = 1.33  # Target R for full exit of remaining position
    be_offset_pct: float = 0.05  # Buffer above entry (0.05%) to cover fees
    underwater_bars_limit: int = 12  # Bars held before checking kinetic stagnancy
    underwater_r_threshold: float = -0.15  # Max R to trigger stagnancy exit
    use_trailing_stop: bool = False  # Dynamic trailing stop flag
    trailing_activation_r: float = 1.0  # R threshold to activate trailing
    trailing_callback_r: float = 0.3  # Distance behind peak R for trailing stop
    exit_mode: str = "stepped"  # "stepped" (default: stepped scaling TP1/BE/TP2/stagnancy) or "trailing_1r"
    trail_dist_r: float = 1.0  # Distance in R behind extreme since entry for trailing_1r mode
