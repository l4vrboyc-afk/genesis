"""
numpy-type hypothesis test for the scalper's order_send None +
'Unnamed arguments not allowed'.

MetaTrader5's C extension accepts only pure Python float/int/str for
request fields. The scalper's strategy math flows out of pandas:
  atr = latest.get("atr_14")       # numpy.float64
  sl  = entry - (atr * 0.8)        # numpy.float64
  round(sl, digits)                 # numpy.float64  (numpy keeps the type)
So the bot ships np.float64 in sl/tp. This probe compares the SAME
request built with pure-Python floats vs np.float64 fields.

Terminal autotrading is OFF (returns retcode 10027), so nothing fills.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT / ".env.scalper", override=True)
import MetaTrader5 as mt5
import numpy as np

ok = mt5.initialize(login=int(os.environ["MT5_LOGIN"]), password=os.environ["MT5_PASSWORD"],
                   server=os.environ["MT5_SERVER"], path=os.environ.get("MT5_PATH"))
print("PY:", sys.executable, "| init:", ok, mt5.last_error())
sys.exit(0) if not ok else None

sym = "EURUSD"
si = mt5.symbol_info(sym); tk = mt5.symbol_info_tick(sym)
ask_p = tk.ask                     # pure Python float from MT5
ask_n = np.float64(ask_p)          # numpy scalar, like pandas/latest.get(...)

sl_dist = 0.00007
filling = mt5.ORDER_FILLING_FOK  # EURUSD supports FOK here

def req(price, sl, tp, volume, comment="Genesis Bot"):
    return {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": volume,
            "type": mt5.ORDER_TYPE_BUY, "price": price, "sl": sl, "tp": tp,
            "deviation": 20, "magic": 202406, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling}

print("ask=", ask_p, "| ask type:", type(ask_p).__name__,
      "| np:", type(ask_n).__name__)
print("=" * 70)

print("P (pure Python floats):")
r = mt5.order_send(req(ask_p, round(ask_p - sl_dist, 5), round(ask_p + sl_dist*1.5, 5), 107.51))
print("   result=", r, "| retcode=", getattr(r, "retcode", None), "| last_error=", mt5.last_error())

print("N (numpy.float64 fields, as pandas/latest would give):")
r = mt5.order_send(req(np.float64(ask_p), np.float64(round(ask_p - sl_dist, 5)),
                        np.float64(round(ask_p + sl_dist*1.5, 5)), np.float64(107.51)))
print("   result=", r, "| retcode=", getattr(r, "retcode", None), "| last_error=", mt5.last_error())

print("N2 (numpy price+sl+tp, python volume):")
r = mt5.order_send(req(np.float64(ask_p), np.float64(round(ask_p - sl_dist, 5)),
                        np.float64(round(ask_p + sl_dist*1.5, 5)), 107.51))
print("   result=", r, "| retcode=", getattr(r, "retcode", None), "| last_error=", mt5.last_error())

mt5.shutdown()
