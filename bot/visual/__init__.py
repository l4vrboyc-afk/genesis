"""
Genesis Visual Engine — MT5 chart markup via companion MQL5 EA.

Provides a clean Python API for drawing entry, stop-loss, take-profit
lines and HUD labels on MetaTrader 5 charts. Since the MT5 Python API
does not expose chart object functions, the visual engine writes
instructions to a shared JSON file that a companion MQL5 EA
(``genesis_visualizer.mq5``) reads and renders in real time.
"""

from .visual_engine import GenesisVisualEngine

__all__ = ["GenesisVisualEngine"]
