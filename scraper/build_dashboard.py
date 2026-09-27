#!/usr/bin/env python3
"""Embed data/trades.jsonl into dashboard/template.html -> dist/index.html."""
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES = os.path.join(ROOT, "data", "trades.jsonl")
STATE = os.path.join(ROOT, "data", "state.json")
TEMPLATE = os.path.join(ROOT, "dashboard", "template.html")
OUT = os.path.join(ROOT, "dist", "index.html")

COLS = ["id", "type", "token", "name", "rarity", "rank", "lineage", "origin", "talent", "level", "bnb", "usd", "t"]


def main(trades_path=TRADES, out_path=OUT):
    rows = []
    if os.path.exists(trades_path):
        with open(trades_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    rows.append([r.get(c) for c in COLS])
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    payload = {
        "cols": COLS,
        "rows": rows,
        "built": int(time.time()),
        "lastRun": state.get("last_run"),
        "siteTotal": state.get("site_total"),
    }
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = html.replace("/*__DATA__*/null", data)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("built %s (%d trades, %d KB)" % (out_path, len(rows), len(html) // 1024))


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3:
        main(sys.argv[1], sys.argv[2])
    else:
        main()
