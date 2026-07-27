"""
Exact-value repro of the scalper's order_send None + 'Unnamed arguments
not allowed'. The bot's MT5 (5.0.5735 loaded from .venv) is identical to
this .venv interpreter's, and initialize+order_send share the executor
thread, so the remaining suspects are the bot's specific REQUEST VALUES:
  - real price (not 0)
  - tiny M1 SL/TP (distance ~0.00007)
  - huge volume (35-113 lots)
  - comment "Genesis Bot"

Terminal has autotrading OFF (probe returned retcode 10027), so anything
that reaches the server is rejected WITHOUT a fill -> SAFE to use real
prices here.

Matrix (all on EURUSD, BUY at ask):
  M1  bot-exact-ish       vol=107.51, SL dist=0.00007, comment 'Genesis Bot'
  M2  volume contrast     vol=0.01,   SL dist=0.00007
  M3  SL contrast         vol=107.51, SL dist=0.00300  (normal ~300-pt stop)
  M4  type_filling FOK(0) vs RETURN(2) — does a non-advertised filling reject?
  W1  bot-exact on a worker thread — confirm/kill thread theory
"""
from __future__ import annotations
import os, sys, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT / ".env.scalper", override=True)
import MetaTrader5 as mt5

print("PY:", sys.executable, "| MT5:", mt5.__file__)
ok = mt5.initialize(login=int(os.environ["MT5_LOGIN"]), password=os.environ["MT5_PASSWORD"],
                   server=os.environ["MT5_SERVER"], path=os.environ.get("MT5_PATH"))
print("init:", ok, mt5.last_error()); sys.exit(0) if not ok else None

sym = "EURUSD"
si = mt5.symbol_info(sym); tk = mt5.symbol_info_tick(sym)
mask = si.filling_mode or 0
fok = mt5.ORDER_FILLING_FOK; ioc = mt5.ORDER_FILLING_IOC; ret = mt5.ORDER_FILLING_RETURN
chosen = (ioc if mask & 2 else (fok if mask & 1 else ret))
print(f"{sym} digits={si.digits} vol_min={si.volume_min} vol_max={si.volume_max} "
      f"vol_step={si.volume_step} stops_level={si.trade_stops_level} filling_mode={mask} -> chosen={chosen}")
ask = tk.ask
print(f"ask={ask}")
print("=" * 70)

def build(volume, sl_dist, filling, comment="Genesis Bot", magic=202406):
    sl = round(ask - sl_dist, si.digits)
    tp = round(ask + sl_dist * 1.5, si.digits)
    return {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": volume,
            "type": mt5.ORDER_TYPE_BUY, "price": ask, "sl": sl, "tp": tp,
            "deviation": 20, "magic": magic, "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling}, sl, tp

def send(label, req, sl, tp):
    r = mt5.order_send(req)
    le = mt5.last_error()
    rc = getattr(r, "retcode", None)
    tag = "None+UNNAMED" if (r is None and "Unnamed" in str(le)) else ("retcode="+str(rc) if r is not None else "None-other")
    print(f"{label:<6} vol={req['volume']:<7} filling={req['type_filling']} sl={sl} tp={tp}")
    print(f"       -> {tag} | last_error={le}")
    if r is not None:
        print(f"       retcode={rc} comment={getattr(r,'comment',None)!r}")

try:
    r1,sl1,tp1 = build(107.51, 0.00007, chosen);  send("M1", r1, sl1, tp1)
    r2,sl2,tp2 = build(0.01,   0.00007, chosen);  send("M2", r2, sl2, tp2)
    r3,sl3,tp3 = build(107.51, 0.00300, chosen);  send("M3", r3, sl3, tp3)
    r4,sl4,tp4 = build(107.51, 0.00007, ret);     send("M4", r4, sl4, tp4)
except Exception as e:
    print("send loop raised:", repr(e))

print("=" * 70)
def worker():
    r,sl,tp = build(107.51, 0.00007, chosen)
    res = mt5.order_send(r); le = mt5.last_error()
    print(f"W1(<- worker thread {threading.get_ident()}) vol=107.51")
    print(f"    -> result={res} retcode={getattr(res,'retcode',None)} last_error={le}")
print(f"main thread id {threading.get_ident()}")
t = threading.Thread(target=worker); t.start(); t.join()
mt5.shutdown()
