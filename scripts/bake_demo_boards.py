"""Pre-compute the replay boards so a demonstration never waits on a network.

    python scripts/bake_demo_boards.py

A cold watch board costs somewhere between two and five minutes: twenty-two
coordinates of weather, twenty-two district assessments behind the exposure
figures, and a hydrological simulation for anything that fires. That is fine for
a control room that loads it once an hour. It is fatal in front of a panel, where
a spinner lasting four minutes ends the conversation regardless of what would
have appeared at the end of it.

So every replay date is computed once, here, and written to disk. At runtime a
replayed board is read from a file in milliseconds and needs no network at all.

This is not a shortcut around the physics. It is the same pipeline, on the same
archive inputs, producing the same numbers - a replayed day cannot change, so
there is nothing to recompute. Re-run this only when the model itself changes.

The live board is untouched and stays live.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core import watch  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "backend", "app",
                       "data", "boards")

# The dates offered in the interface. Each is a day something actually happened,
# so the board can be shown doing its job rather than described doing it.
DATES = [
    ("2024-07-30", "Wayanad landslide"),
    ("2022-06-17", "Assam floods"),
    ("2023-07-09", "North India floods"),
    ("2020-07-15", "Assam floods"),
]


def main() -> int:
    print("Baking replay boards - the same pipeline, written down once")
    os.makedirs(OUT_DIR, exist_ok=True)

    ok, failed = 0, []
    for date, label in DATES:
        # An existing board is left alone. Re-running to fill one gap must not
        # risk losing three that already worked to a rate limit.
        if os.path.exists(os.path.join(OUT_DIR, "%s.json" % date)):
            print("  %-12s %-22s already baked, skipping" % (date, label))
            ok += 1
            continue

        t0 = time.time()
        board = None
        # Open-Meteo rate-limits, and a 429 is a pause rather than a refusal.
        for attempt in range(3):
            try:
                board = watch.build(with_river=True, date=date)
            except Exception as exc:
                print("  %-12s attempt %d failed (%s)" % (date, attempt + 1, exc))
                board = None
            if board is not None and board.live:
                break
            if attempt < 2:
                wait = 30 * (attempt + 1)
                print("  %-12s backing off %ds" % (date, wait))
                time.sleep(wait)

        if board is None or not board.live:
            reason = board.reason[:40] if board is not None else "exception"
            print("  %-12s %-22s no data (%s)" % (date, label, reason))
            failed.append(date)
            continue

        blob = board.to_dict()
        blob["baked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        blob["event_label"] = label
        path = os.path.join(OUT_DIR, "%s.json" % date)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blob, fh, separators=(",", ":"))

        c = blob["counts"]
        print("  %-12s %-22s %d need attention (%d act, %d prepare) "
              "in %.0fs -> %.0f KB"
              % (date, label, c["needing_attention"], c["act"], c["prepare"],
                 time.time() - t0, os.path.getsize(path) / 1e3))
        ok += 1

    index = os.path.join(OUT_DIR, "index.json")
    with open(index, "w", encoding="utf-8") as fh:
        json.dump({
            "note": ("Replay boards, pre-computed. A replayed day cannot "
                     "change, so these are identical to what the pipeline "
                     "produces live from the archive - they simply do not make "
                     "anyone wait for it."),
            "dates": [{"date": d, "label": l} for d, l in DATES
                      if d not in failed],
        }, fh, separators=(",", ":"))

    print("\n  %d baked, %d failed" % (ok, len(failed)))
    if failed:
        print("  failed: %s - re-run to fill them in" % ", ".join(failed))
    print("  -> %s" % os.path.abspath(OUT_DIR))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
