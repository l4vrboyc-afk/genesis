"""
Standalone diagnostic for the scalper's `order_send -> None, code=-2,
'Unnamed arguments not allowed'` failure.

Run under EACH interpreter and compare output:
    uv  CPython3.11 (the bot's runtime):
      C:\\Users\\Moses Egbunike\\AppData\\Roaming\\uv\\python\\cpython-3.11-windows-x86_64-none\\python.exe scratch\\probe_order_send.py
    project .venv:
      .venv\\Scripts\\python.exe scratch\\probe_order_send.py

It mounts NO real order. Probe A is an intentionally incomplete request;
Probe B is the project's *exact* request shape but with price=0 so the
trade server rejects it (retcode 10016) IF the request ever reaches the
server. The discriminator:

    None  + "Unnamed arguments not allowed"   -> rejected pre-server
                                                     = binding rejects the
                                                       DICT form (form/version)
    <result>.retcode == 10016 (or any retcode) -> request reached the server
                                                     = dict form is accepted;
                                                       the scalper's live
                                                       failure is per-value
"""
from __future__ import annotations
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.scalper", override=True)
except Exception as e:
    print("dotenv load failed:", e)

import MetaTrader5 as mt5

print("PYTHON :", sys.executable)
print("MT5.py :", getattr(mt5, "__file__", "?"))
try:
    import importlib.metadata as m
    print("MT5 ver:", m.version("MetaTrader5"))
except Exception as e:
    print("MT5 ver: <unavailable>", e)
print("-" * 60)

login = int(os.environ.get("MT5_LOGIN", "0"))
password = os.environ.get("MT5_PASSWORD", "")
server = os.environ.get("MT5_SERVER", "")
path = os.environ.get("MT5_PATH", "")

init = mt5.initialize(login=login, password=password, server=server, path=path)
print("initialize:", init, "| last_error:", mt5.last_error())
if not init:
    sys.exit(1)

acc = mt5.account_info()
print("account:", getattr(acc, "login", None), getattr(acc, "server", None))
print("-" * 60)

sym = "EURUSD"
si = mt5.symbol_info(sym)
tk = mt5.symbol_info_tick(sym)
print(f"{sym}: filling_mode={si.filling_mode} digits={si.digits} "
      f"vol_min={si.volume_min} vol_max={si.volume_max} vol_step={si.volume_step} "
      f"stops_level={si.trade_stops_level}")
mask = si.filling_mode or 0
filling = (mt5.ORDER_FILLING_IOC if mask & 2
           else (mt5.ORDER_FILLING_FOK if mask & 1
                 else mt5.ORDER_FILLING_RETURN))
print(f"chosen filling = {filling}  (0=FOK,1=IOC,2=RETURN)")
print("-" * 60)

# ── Probe A: incomplete request (cannot place an order) ─────────────
print("A. incomplete request:")
ra = mt5.order_send({"action": mt5.TRADE_ACTION_DEAL})
print("   result =", ra, "| last_error =", mt5.last_error())

# ── Probe B: project-exact shape, price=0 -> server reject if reached ─
print("B. project-exact dict, price=0 (invalid -> server reject if reached):")
req = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": sym,
    "volume": 0.01,
    "type": mt5.ORDER_TYPE_BUY,
    "price": 0.0,
    "sl": 0.0,
    "tp": 0.0,
    "deviation": 20,
    "magic": int(os.environ.get("MAGIC_NUMBER", "0") or 0),
    "comment": "probe",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": filling,
}
print("   request =", req)
rb = mt5.order_send(req)
print("   result =", rb,
      "| retcode =", getattr(rb, "retcode", None),
      "| last_error =", mt5.last_error())

# ── Probe C: does a TradeRequest namedtuple object exist? ──────────
print("C. mt5.TradeRequest present?:", hasattr(mt5, "TradeRequest"))

mt5.shutdown()
