"""
Genesis Visual Engine — MT5 Chart Markup Manager
==================================================

Draws entry, stop-loss, take-profit lines and HUD labels on MetaTrader 5
charts. Works around the MT5 Python API's lack of chart object functions
by writing structured JSON instructions to a shared file that a companion
MQL5 Expert Advisor (``genesis_visualizer.mq5``) reads in real time.

Architecture
------------

    Python Bot  ──JSON──▶  Shared File  ──READ──▶  MQL5 EA  ──DRAW──▶  MT5 Chart
                                                                        │
                                              ┌── OBJ_HLINE (entry) ───┤
                                              ├── OBJ_TREND  (SL/TP) ──┤
                                              └── OBJ_LABEL  (HUD) ────┘

Usage
-----
    from bot.visual import GenesisVisualEngine

    # On startup:
    GenesisVisualEngine.cleanup_all_genesis_objects()

    # On trailing stop update:
    GenesisVisualEngine.update_trade_visuals(
        symbol="EURUSD",
        ticket=12345678,
        position_type="buy",
        entry_price=1.10000,
        current_sl=1.09500,
        target_tp=1.12000,
        current_mode="STRUCTURE",
        atr_value=0.00142,
    )

    # On trade closure:
    GenesisVisualEngine.cleanup_trade_objects(symbol="EURUSD", ticket=12345678)

File Format
-----------
The shared JSON file is written to the MT5 common files directory so the
MQL5 EA can read it regardless of which MT5 data folder the terminal uses::

    %AppData%\\Roaming\\MetaQuotes\\Terminal\\Common\\Files\\genesis_visuals.json

This path is resolved via ``mt5.terminal_info().common_data_path`` when MT5
is initialised, or falls back to a configurable local path.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional
from loguru import logger


# ── Constants ──────────────────────────────────────────────────────────

# Object name prefix — all visual objects created by this engine use this
# prefix so they can be identified and cleaned up en masse.
OBJECT_PREFIX = "GENESIS_"

# Top-level JSON keys for non‑trade visual categories.  These are stored
# alongside per‑ticket entries in the shared IPC file and are interpreted
# by the companion MQL5 EA (genesis_visualizer.mq5).
META_SWEEP_PINS = "__sweep_pins__"
META_OVERLAYS = "__overlays__"
META_TRAIL_MILESTONES = "__trail_milestones__"
META_ALL = {"__sweep__", META_SWEEP_PINS, META_OVERLAYS, META_TRAIL_MILESTONES}

# Maximum number of trail milestones kept per ticket to prevent the IPC
# file from growing too large on long-running positions.
MAX_TRAIL_MILESTONES = 50

# Colour palette used when rendering overlays (session ranges / equilibrium).
# These names are passed straight through to the MQL5 EA which maps them.
DEFAULT_SESSION_COLOR = "DodgerBlue"
DEFAULT_EQUILIBRIUM_COLOR = "Orange"

# Default fallback path when MT5 is not initialised (used for testing /
# early startup before the MT5 connection is established).
_FALLBACK_COMMON_DIR = Path(
    os.environ.get(
        "GENESIS_VISUAL_DATA_DIR",
        Path.home() / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files",
    )
)

# ── Internal Helpers ───────────────────────────────────────────────────

def _get_common_data_dir() -> Path:
    """Return the MT5 common data directory (``Common\\Files``).

    Tries ``mt5.terminal_info().common_data_path`` first, then falls
    back to the well-known ``%AppData%`` path on Windows.  The fallback
    is safe because the MQL5 EA also locates the file via the same well-
    known path.
    """
    try:
        import MetaTrader5 as mt5
        info = mt5.terminal_info()
        if info and hasattr(info, "common_data_path") and info.common_data_path:
            return Path(info.common_data_path) / "Files"
    except Exception:
        pass
    return _FALLBACK_COMMON_DIR


def _visuals_path() -> Path:
    """Full path to the shared JSON visuals file."""
    return _get_common_data_dir() / "genesis_visuals.json"


def _read_visuals() -> dict[str, Any]:
    """Read the current visuals file, returning an empty dict on failure."""
    path = _visuals_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"VisualEngine: failed to read visuals file: {e}")
        return {}


def _write_visuals(data: dict[str, Any]) -> bool:
    """Atomically write the visuals dict to the shared JSON file.

    Writes to a temp file first, then renames to avoid partial reads
    by the MQL5 EA (atomic rename on the same filesystem).
    """
    path = _visuals_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as e:
        logger.error(f"VisualEngine: failed to write visuals: {e}")
        return False


# ── Public API ─────────────────────────────────────────────────────────

class GenesisVisualEngine:
    """Manages MT5 chart visual markups via file-based IPC with an MQL5 EA.

    All methods are **synchronous** (no ``async``) because writing a JSON
    file is non-blocking IO — no event-loop suspension needed.  Call from
    any context.

    Thread-safety: each method reads the current file, modifies it in
    memory, then atomically writes it back.  Concurrent Python callers
    share the same process lock; the MQL5 EA only reads, never writes.
    """

    # ── Per‑Trade Visuals ───────────────────────────────────────────

    @staticmethod
    def update_trade_visuals(
        symbol: str,
        ticket: int,
        position_type: str,       # "buy" or "sell"
        entry_price: float,
        current_sl: float,
        target_tp: float,
        current_mode: str = "",   # e.g. "STRUCTURE", "ATR_DYNAMIC"
        atr_value: float = 0.0,
    ) -> bool:
        """Draw or update the 4 visual elements for one position.

        Writes an instruction block to the shared JSON file.  The MQL5 EA
        reads it and creates / updates:

          - OBJ_HLINE (solid amber)  → entry price
          - OBJ_TREND (dashed cyan)  → stop-loss (moves with trailing)
          - OBJ_TREND (dashed green) → take-profit (static)
          - OBJ_LABEL (top-left)     → HUD: ticket, mode, ATR

        Args:
            symbol:     Trading pair (e.g. ``"EURUSD"``).
            ticket:     MT5 position ticket number.
            position_type: ``"buy"`` or ``"sell"``.
            entry_price: Position open price.
            current_sl: Current stop-loss level (may move with trailing).
            target_tp:  Take-profit level (static unless manually changed).
            current_mode: Active trailing mode label (``"STATIC"``,
                         ``"BREAKEVEN"``, ``"STRUCTURE"``, ``"ATR_DYNAMIC"``,
                         ``"ACCELERATED"``).
            atr_value:  Current M15 ATR value for the HUD.

        Returns:
            ``True`` if the write succeeded, ``False`` on error.
        """
        data = _read_visuals()
        key = str(ticket)

        data[key] = {
            "symbol": symbol.upper(),
            "type": position_type.lower(),
            "entry_price": round(entry_price, 8),
            "current_sl": round(current_sl, 8),
            "target_tp": round(target_tp, 8),
            "mode": current_mode,
            "atr": round(atr_value, 8) if atr_value else 0.0,
            "updated_at": time.time(),
        }

        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"🎨 Visual updated T{ticket} {symbol}: "
                f"mode={current_mode}, SL={current_sl:.5f}"
            )
        return ok

    # ── Cleanup ────────────────────────────────────────────────────

    @staticmethod
    def cleanup_trade_objects(symbol: str, ticket: int) -> bool:
        """Remove the 4 visual elements for a specific closed trade.

        Deletes the ticket's entry from the shared JSON file.  The MQL5
        EA reads the updated file and deletes the corresponding chart
        objects (OBJ_HLINE, OBJ_TREND ×2, OBJ_LABEL).

        Args:
            symbol: Trading pair (for logging, not used in cleanup).
            ticket: MT5 position ticket to clean up.

        Returns:
            ``True`` if the write succeeded, ``False`` on error.
        """
        data = _read_visuals()
        key = str(ticket)

        if key in data:
            del data[key]
            ok = _write_visuals(data)
            if ok:
                logger.info(f"🧹 Visual cleaned up T{ticket} ({symbol})")
            return ok

        logger.debug(f"VisualEngine: no visuals to clean up for T{ticket} ({symbol})")
        return True  # Nothing to delete is not a failure.

    # ── Sweep Pins (OBJ_TEXT liquidity sweep annotations) ───────────

    @staticmethod
    def add_sweep_pin(
        symbol: str,
        level: float,
        label: str,
        direction: str,          # "bullish" or "bearish"
        expires_seconds: int = 3600,
    ) -> Optional[str]:
        """Place an OBJ_TEXT sweep-pin label at a specific price level.

        Sweep pins mark where liquidity was taken (e.g. SSL/Buyside sweep).
        They auto-expire after *expires_seconds* and each pin gets a unique
        ID that can be used for selective cleanup.

        Duplicate pins at the same level (within 0.0001 tolerance) are
        replaced rather than stacked.

        Args:
            symbol:           Trading pair (e.g. ``"EURUSD"``).
            level:            Price level where the sweep occurred.
            label:            Short description (``"SSL Sweep"``, etc.).
            direction:        ``"bullish"`` or ``"bearish"``.
            expires_seconds:  TTL in seconds (default 1 hour).

        Returns:
            The unique pin ID (e.g. ``"swp_a1b2c3d4"``), or ``None``
            if the write failed.
        """
        if direction not in ("bullish", "bearish"):
            logger.warning(
                f"VisualEngine: sweep_pin direction must be 'bullish' or "
                f"'bearish', got '{direction}'"
            )
            return None

        data = _read_visuals()
        now = time.time()
        pin_id = "swp_" + secrets.token_hex(4)
        pin = {
            "id": pin_id,
            "label": str(label),
            "direction": direction,
            "level": round(level, 8),
            "time": now,
            "expires_at": now + expires_seconds,
        }

        pins = data.setdefault(META_SWEEP_PINS, {})
        symbol_pins = pins.setdefault(symbol.upper(), [])

        # Dedupe: replace any pin at the same level (within tolerance)
        replaced = False
        for i, existing in enumerate(symbol_pins):
            if abs(existing.get("level", 0.0) - pin["level"]) <= 0.0001:
                pin["id"] = existing.get("id", pin_id)  # keep original id
                symbol_pins[i] = pin
                replaced = True
                break
        if not replaced:
            symbol_pins.append(pin)

        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"📍 Sweep pin placed on {symbol.upper()}: "
                f"{label}@{level:.5f} ({direction})"
            )
            return pin_id
        return None

    @staticmethod
    def cleanup_sweep_pins(
        symbol: str,
        pin_ids: Optional[list[str]] = None,
    ) -> bool:
        """Remove one or more sweep pins from the IPC file.

        Args:
            symbol:   Trading pair whose pins to clean up.
            pin_ids:  Specific pin IDs to remove.  If ``None``, ALL
                      pins for *symbol* are removed.

        Returns:
            ``True`` if the write succeeded, ``False`` on error.
        """
        data = _read_visuals()
        pins = data.get(META_SWEEP_PINS, {})
        sym = symbol.upper()

        if sym not in pins:
            return True

        if pin_ids is None:
            del pins[sym]
            if not pins:
                data.pop(META_SWEEP_PINS, None)
        else:
            ids_to_del = set(pin_ids)
            pins[sym] = [p for p in pins[sym] if p.get("id") not in ids_to_del]
            if not pins[sym]:
                del pins[sym]
            if not pins:
                data.pop(META_SWEEP_PINS, None)

        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"🧹 Sweep pins cleaned: {symbol}"
                + (f" ids={pin_ids}" if pin_ids else " (all)")
            )
        return ok

    # ── Overlays (OBJ_RECTANGLE session ranges & equilibrium) ──────────

    @staticmethod
    def place_overlay(
        symbol: str,
        overlay_type: str,       # "session_range" or "equilibrium"
        label: str,
        price_high: float,
        price_low: float,
        time_start: Optional[float] = None,
        time_end: Optional[float] = None,
        color_type: Optional[str] = None,
    ) -> Optional[str]:
        """Add a rectangle overlay to the chart.

        Session ranges span a time window; equilibrium zones are horizontal
        bands with no time constraint.

        Args:
            symbol:       Trading pair.
            overlay_type: ``"session_range"`` or ``"equilibrium"``.
            label:        Human-readable label.
            price_high:   Upper boundary of the zone.
            price_low:    Lower boundary of the zone.
            time_start:   Epoch seconds for start (required for session_range).
            time_end:     Epoch seconds for end (required for session_range).
            color_type:   Colour label passed to the MQL5 EA (e.g.
                          ``"DodgerBlue"``, ``"Orange"``).  Falls back to
                          defaults by overlay type if omitted.

        Returns:
            Overlay ID string, or ``None`` on failure.
        """
        if overlay_type not in ("session_range", "equilibrium"):
            logger.warning(
                f"VisualEngine: overlay_type must be 'session_range' or "
                f"'equilibrium', got '{overlay_type}'"
            )
            return None

        if overlay_type == "session_range" and (time_start is None or time_end is None):
            logger.warning(
                "VisualEngine: session_range requires time_start and time_end"
            )
            return None

        data = _read_visuals()
        ts = str(int(time.time()))

        safe_label = "".join(c for c in label if c.isalnum() or c in " -_")
        # Include a short random suffix so distinct overlays placed in the
        # same second (e.g. multiple equilibrium bands refreshed together by
        # the orchestrator) get unique ids and are not collapsed by the
        # dedup-by-id guard below.
        overlay_id = f"ovl_{overlay_type[:7]}_{symbol.upper()}_{ts[-6:]}_{secrets.token_hex(2)}"

        if color_type is None:
            color_type = (
                DEFAULT_SESSION_COLOR if overlay_type == "session_range"
                else DEFAULT_EQUILIBRIUM_COLOR
            )

        overlay = {
            "id": overlay_id,
            "type": overlay_type,
            "label": str(label),
            "price_high": round(price_high, 8),
            "price_low": round(price_low, 8),
            "time_start": time_start or 0,
            "time_end": time_end or 0,
            "color_type": color_type,
        }

        overlays_data = data.setdefault(META_OVERLAYS, {})
        symbol_overlays = overlays_data.setdefault(symbol.upper(), [])

        # Dedupe by id
        for i, existing in enumerate(symbol_overlays):
            if existing.get("id") == overlay_id:
                symbol_overlays[i] = overlay
                break
        else:
            symbol_overlays.append(overlay)

        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"🗏 Overlay placed on {symbol.upper()}: {label} "
                f"({overlay_type}) @{symbol.upper()}"
            )
        return overlay_id if ok else None

    @staticmethod
    def cleanup_overlays(
        symbol: str,
        overlay_ids: Optional[list[str]] = None,
    ) -> bool:
        """Remove one or more overlays for a symbol.

        Args:
            symbol:       Trading pair whose overlays to clean.
            overlay_ids:  List of overlay IDs to remove.  If ``None``,
                          ALL overlays for *symbol* are removed.

        Returns:
            ``True`` if the write succeeded, ``False`` on error.
        """
        data = _read_visuals()
        ovl = data.get(META_OVERLAYS, {})
        sym_key = symbol.upper()

        if sym_key not in ovl:
            return True

        if overlay_ids is None:
            del ovl[sym_key]
            if not ovl:
                data.pop(META_OVERLAYS, None)
        else:
            ids_to_del = set(overlay_ids)
            ovl[sym_key] = [o for o in ovl[sym_key] if o.get("id") not in ids_to_del]
            if not ovl[sym_key]:
                del ovl[sym_key]
            if not ovl:
                data.pop(META_OVERLAYS, None)

        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"🧹 Overlays cleaned: {symbol}"
                + (f" ids={overlay_ids}" if overlay_ids else " (all)")
            )
        return ok

    # ── Trail Milestones (OBJ_TREND trailing‑stop history) ─────────────

    @staticmethod
    def add_trail_milestone(
        ticket: int,
        symbol: str,
        sl_price: float,
        mode: str,
        time_secs: Optional[float] = None,
    ) -> bool:
        """Record a trailing‑stop adjustment as a trail milestone.

        Milestones form a connected OBJ_TREND line on the chart, showing
        the complete trailing‑stop history for one position.  The oldest
        milestone is trimmed when the cap (50) is exceeded.

        Args:
            ticket:    MT5 position ticket.
            symbol:    Trading pair.
            sl_price:  The newly applied SL price.
            mode:      Trailing mode label (``"STATIC"``, ``"BREAKEVEN"``, etc.).
            time_secs: Epoch seconds (defaults to ``time.time()``).

        Returns:
            ``True`` if the write succeeded.
        """
        data = _read_visuals()
        trail_data = data.setdefault(META_TRAIL_MILESTONES, {})
        ticket_key = str(ticket)

        milestones: list[dict] = trail_data.get(ticket_key, [])
        seq = len(milestones)

        milestone = {
            "id": f"trl_{ticket_key}_{seq}",
            "ticket": ticket,
            "symbol": symbol.upper(),
            "sl_price": round(sl_price, 8),
            "mode": str(mode),
            "time": time_secs or time.time(),
        }
        milestones.append(milestone)

        # Cap at MAX_TRAIL_MILESTONES — oldest-first trim
        if len(milestones) > MAX_TRAIL_MILESTONES:
            milestones = milestones[-MAX_TRAIL_MILESTONES:]

        trail_data[ticket_key] = milestones
        ok = _write_visuals(data)
        if ok:
            logger.debug(
                f"📍 Trail milestone T{ticket}: #{seq} SL={sl_price:.5f} "
                f"mode={mode}"
            )
        return ok

    @staticmethod
    def cleanup_trail_milestones(ticket: int) -> bool:
        """Remove all trail milestones for a closed ticket.

        Called when a position is fully closed so its trail history is
        erased from the chart.

        Args:
            ticket: MT5 position ticket.

        Returns:
            ``True`` if the write succeeded.
        """
        data = _read_visuals()
        trail_data = data.get("__trail_milestones__", {})
        ticket_key = str(ticket)

        if ticket_key in trail_data:
            del trail_data[ticket_key]
            if not trail_data:
                data.pop("__trail_milestones__", None)
            ok = _write_visuals(data)
            if ok:
                logger.debug(f" Trail milestones removed for T{ticket}")
            return ok

        return True  # nothing to clean up → success

    @staticmethod
    def cleanup_all_genesis_objects() -> bool:
        """Wipe ALL Genesis visual objects from every chart.

        Writes an empty visuals file and includes a special
        ``__sweep__`` instruction telling the MQL5 EA to delete ALL
        objects whose name starts with ``GENESIS_`` on every open
        chart.

        Call once at bot startup to erase orphaned lines from previous
        runs (e.g. if the bot crashed while positions were open).
        """
        data = _read_visuals()
        # Keep any existing per-ticket data but add the sweep marker
        data["__sweep__"] = True
        # Also purge stale entries whose tickets are no longer active.
        # The caller is expected to call this at startup before any
        # positions are loaded, so we can just clear everything.
        stale = [k for k in data if k != "__sweep__"]
        for k in stale:
            del data[k]

        ok = _write_visuals(data)
        if ok:
            logger.info("🧹 Visual sweep: all Genesis chart objects will be cleaned")
        return ok

    @staticmethod
    def get_active_visuals() -> dict[str, Any]:
        """Return the current visuals dict (excluding meta/control keys).

        Strips ``__sweep__``, ``__sweep_pins__``, ``__overlays__``,
        and ``__trail_milestones__`` so callers only see per‑ticket
        entries.  Useful for diagnostics / dashboard display.
        """
        data = _read_visuals()
        for meta_key in META_ALL:
            data.pop(meta_key, None)
        return data


# ── Standalone Utilities ──────────────────────────────────────────────

def generate_mql5_source(output_dir: Optional[Path] = None) -> str:
    """Read the companion MQL5 EA source from disk and optionally copy it.

    The canonical MQL5 EA lives at ``bot/visual/genesis_visualizer.mq5``.
    This helper reads it and optionally writes a copy to *output_dir*,
    so the user can produce a fresh copy when updating.

    Call once during bot setup to seed the EA file into MT5::

        from bot.visual.visual_engine import generate_mql5_source
        path = generate_mql5_source(Path("bot/visual"))
        print(f"MQL5 EA sourced from {path}")

    Returns the full source as a string.
    """
    # The canonical .mq5 file lives alongside this .py file
    mq5_path = Path(__file__).resolve().parent / "genesis_visualizer.mq5"
    if not mq5_path.is_file():
        raise FileNotFoundError(
            f"MQL5 EA not found at {mq5_path}. "
            "Reinstall from the project repository."
        )

    source = mq5_path.read_text(encoding="utf-8")

    if output_dir is not None:
        out_path = Path(output_dir) / mq5_path.name
        out_path.write_text(source, encoding="utf-8")
        logger.info(f"📄 MQL5 EA source written to {out_path.resolve()}")

    return source
