"""
trade_manager.py - DEPRECATED / STUB
=====================================
This module is NOT active.

All trading logic runs in executor.py:
  - start_auto_trading() launches the trading loop
  - manage_position() handles milestones, BE, TP, trailing
  - auto_trade_loop() scans and executes every 60 seconds

This file is kept to avoid import errors.
"""

def notify(message: str) -> None:
    """Stub — notifications not implemented."""
    print(f"[NOTIFY] {message}")


def monitor() -> None:
    """
    DEPRECATED — trading loop runs in executor.py auto_trade_loop().
    This function is never called.
    """
    raise RuntimeError(
        "trade_manager.monitor() is deprecated. "
        "Trading loop runs in executor.start_auto_trading()."
    )
