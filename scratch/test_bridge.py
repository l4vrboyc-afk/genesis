"""Diagnostic: does the pywebview bridge appear after page load?"""
import webview
import threading
import time
import os

picker = os.path.abspath(
    r"C:\Users\Moses Egbunike\Documents\Claude Code Projects\Genesis\gui\profile_picker.html"
)
html = open(picker, "r", encoding="utf-8").read()

results = []
done = threading.Event()


def poll_bridge(win, interval=1.0):
    for i in range(60):
        time.sleep(interval)
        if done.is_set():
            break
        try:
            # check for the actual bridge object
            r = win.evaluate_js(
                "typeof window.pywebview === 'undefined' ? 'MISSING' "
                ": (typeof window.pywebview.api === 'undefined' ? 'NO_API' : 'FOUND')"
            )
            results.append((time.time(), i, r))
            print(f"  [{i:2d}] {r}")
            if r == "FOUND":
                break
        except Exception as exc:
            results.append((time.time(), i, f"ERR: {exc}"))
            print(f"  [{i:2d}] evaluate_js error: {exc}")


win = webview.create_window("Bridge Test", html=html, width=960, height=600)

t = threading.Thread(target=poll_bridge, args=(win,), daemon=True)
t.start()

webview.start()

done.set()
t.join(timeout=5)

win.events.closed.set()

print("\n=== BRIDGE TEST RESULTS ===")
for ts, i, r in results:
    print(f"  t={ts:.1f} poll#{i}: {r}")

found = any(r == "FOUND" for _, _, r in results)
print(f"\nBridge ever appeared: {found}")
if not found:
    print(">>> Bridge NEVER appeared — this is the root cause of your issue.")
