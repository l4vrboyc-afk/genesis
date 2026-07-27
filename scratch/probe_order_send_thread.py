"""
Thread-affinity repro for the scalper's order_send failure.

Hypothesis: MetaTrader5's C extension rejects `order_send(dict)` with
None + 'Unnamed arguments not allowed' when the call runs on a DIFFERENT
thread than the one that called `mt5.initialize()`. The bot initializes
on the asyncio/main thread but executes order_send on the Mt5Executor
daemon worker thread -> mismatch -> every order_send None.

This script:
  1. mt5.initialize(...) on the MAIN thread (what the bot's connector does)
  2. mt5.order_send(proj_request, price=0) on the MAIN thread   -> expect retcode (10027)
  3. the SAME call on a threading.Thread                          -> observe None+'Unnamed'?
No order is placed: price=0 forces a server reject (retcode 10016) IF the
binding ever lets the request reach the server.
"""
from __future__ import annotations
import os, sys, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT / ".env.scalper", override=True)

import MetaTrader5 as mt5

print("PYTHON:", sys.executable, "| MT5.py:", mt5.__file__)
ok = mt5.initialize(login=int(os.environ["MT5_LOGIN"]),
                    password=os.environ["MT5_PASSWORD"],
                    server=os.environ["MT5_SERVER"],
                    path=os.environ.get("MT5_PATH"))
print("initialize (main thread):", ok, mt5.last_error())
if not ok:
    sys.exit(1)

si = mt5.symbol_info("EURUSD")
mask = si.filling_mode or 0
filling = (mt5.ORDER_FILLING_IOC if mask & 2 else (mt5.ORDER_FILLING_FOK if mask & 1 else mt5.ORDER_FILLING_RETURN))


def proj_request():
    return {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": "EURUSD", "volume": 0.01,
        "type": mt5.ORDER_TYPE_BUY, "price": 0.0, "sl": 0.0, "tp": 0.0,
        "deviation": 20, "magic": int(os.environ.get("MAGIC_NUMBER", "0") or 0),
        "comment": "probe", "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
    }

print("-" * 60)
print("MAIN-thread order_send:")
r = mt5.order_send(proj_request())
print("   result =", r, "| retcode =", getattr(r, "retcode", None),
      "| last_error =", mt5.last_error())

print("-" * 60)
res = {}
def worker():
    print("WORKER-thread order_send (thread id %d):" % threading.get_ident())
    r = mt5.order_send(proj_request())
    res["result"] = r
    res["last_error"] = mt5.last_error()
    print("   result =", r, "| retcode =", getattr(r, "retcode", None),
          "| last_error =", mt5.last_error())

print("main thread id:", threading.get_ident())
t = threading.Thread(target=worker, name="Mt5ExecutorLike")
t.start(); t.join()
print("-" * 60)
print("VERDICT:",
      "worker-thread order_send returned None with 'Unnamed' -> thread-affinity CONFIRMED"
      if res.get("result") is None and "Unnamed" in str(res.get("last_error"))
      else "worker-thread order_send did NOT reproduce the None+'Unnamed' failure")

mt5.shutdown()
