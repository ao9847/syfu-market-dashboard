#!/usr/bin/env python3
"""Summarise data/trades.jsonl into dist/prices.json for the 資産ノート holdings page.

Output (compact, < 256 KB so it fits one artifact db document):
  seg: per market segment (MANEKINEKO rarity x rank, Genesis, TAIYAKI variant, Right Scroll)
       price = median of the last 7 days, or of the last 10 trades when the week is thin.
  segs: segment keys, in the order tok refers to them
  tok: token id -> [segment index, last bnb, last trade time]
       (a list of those when the same number exists in two collections, e.g. Genesis #993 and MANEKINEKO #993)
"""
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_dashboard import variant_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES = os.path.join(ROOT, "data", "trades.jsonl")
STATE = os.path.join(ROOT, "data", "state.json")
OUT = os.path.join(ROOT, "dist", "prices.json")

DAY = 86400
JST = 9 * 3600
TY_LABEL = {"NORMAL": "TAIYAKI", "WHITE": "TAIYAKI WHITE", "COLLAB": "The IIIRD Card", "NEW": "TAIYAKI (新種)"}


def segment(r):
    t = r.get("type")
    if t == "manekineko":
        rar = r.get("rarity") or "unknown"
        rank = r.get("rank") or "p?"
        return "mk:%s:%s" % (rar, rank), "MANEKINEKO", "%s %s" % (rar.capitalize(), rank.upper())
    if t == "genesis":
        return "genesis", "MANEKINEKO", "Genesis"
    if t == "breed_item":
        v = variant_of(r)
        return "ty:" + v, "TAIYAKI", TY_LABEL.get(v, v)
    if t == "excavation_right":
        return "scroll", "ITEM", "Right Scroll"
    return None, None, None


def med(xs):
    return round(statistics.median(xs), 6) if xs else None


def main():
    rows = []
    with open(TRADES, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["t"])
    now = max(r["t"] for r in rows) if rows else int(time.time())
    today = (now + JST) // DAY

    segs, tok = {}, {}
    for r in rows:
        key, kind, label = segment(r)
        if not key or r.get("bnb") is None:
            continue
        s = segs.setdefault(key, {"kind": kind, "label": label, "trades": []})
        s["trades"].append((r["t"], r["bnb"], r.get("usd")))
        tok.setdefault(str(r["token"]), {})[key] = [key, r["bnb"], r["t"]]

    out_seg = {}
    for key, s in segs.items():
        tr = s["trades"]
        wk = [x for x in tr if x[0] >= now - 7 * DAY]
        m30 = [x for x in tr if x[0] >= now - 30 * DAY]
        basis = wk if len(wk) >= 3 else tr[-10:]
        spark = []
        for d in range(today - 29, today + 1):
            day = [x[1] for x in tr if (x[0] + JST) // DAY == d]
            spark.append(med(day))
        out_seg[key] = {
            "kind": s["kind"],
            "label": s["label"],
            "bnb": med([x[1] for x in basis]),
            "usd": round(statistics.median([x[2] for x in basis if x[2] is not None]), 2) if basis else None,
            "basis": "7d" if basis is wk else "last%d" % len(basis),
            "n7": len(wk),
            "n30": len(m30),
            "lo30": min((x[1] for x in m30), default=None),
            "hi30": max((x[1] for x in m30), default=None),
            "last": [tr[-1][1], tr[-1][2], tr[-1][0]],
            "spark": spark,
        }

    keys = list(out_seg)
    def enc(v):
        return [keys.index(v[0]), v[1], v[2]]
    tok = {k: enc(next(iter(v.values()))) if len(v) == 1 else [enc(x) for x in v.values()] for k, v in tok.items()}
    recent = [r for r in rows[-50:] if r.get("bnb") and r.get("usd")]
    bnb_usd = round(statistics.median(r["usd"] / r["bnb"] for r in recent), 2) if recent else None
    state = {}
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    payload = {
        "built": int(time.time()),
        "lastRun": state.get("last_run"),
        "latestTrade": now,
        "bnbUsd": bnb_usd,
        "segs": keys,
        "seg": out_seg,
        "tok": tok,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print("built %s (%d segments, %d tokens, %d KB)" % (OUT, len(out_seg), len(tok), os.path.getsize(OUT) // 1024))


if __name__ == "__main__":
    main()
