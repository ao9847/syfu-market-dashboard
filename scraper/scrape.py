#!/usr/bin/env python3
"""SyFu Marketplace activity scraper.

Polite by design:
  * one request at a time, fixed interval (default 5s) between requests
  * exponential backoff on errors, aborts after repeated failures
  * daily mode stops as soon as it reaches already-known trades (usually 1 request)
  * backfill mode checkpoints progress so an interrupted run resumes

Modes:
  python scraper/scrape.py backfill   # fetch every page once
  python scraper/scrape.py daily      # fetch only new trades
"""
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = "https://manekineko.syfu.io"
PAGE_URL = BASE + "/activity"
PAGE_SIZE = 20
INTERVAL = float(os.environ.get("SCRAPE_INTERVAL", "5"))
MAX_DAILY_PAGES = int(os.environ.get("MAX_DAILY_PAGES", "30"))
USER_AGENT = os.environ.get(
    "SCRAPE_USER_AGENT",
    "SyFuMarketResearch/1.0 (personal, low-frequency; 1 req per 5s)",
)
ROUTER_STATE = (
    "%5B%22%22%2C%7B%22children%22%3A%5B%5B%22locale%22%2C%22ja%22%2C%22d%22%5D%2C%7B%22children%22"
    "%3A%5B%22(main)%22%2C%7B%22children%22%3A%5B%22activity%22%2C%7B%22children%22%3A%5B%22__PAGE__"
    "%22%2C%7B%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%2C%22modal%22%3A%5B%22__DEFAULT__%22%2C%7B%7D"
    "%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
)
ACTION_NAME = "getMarketActivitiesAction"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
TRADES_PATH = os.path.join(DATA_DIR, "trades.jsonl")
STATE_PATH = os.path.join(DATA_DIR, "state.json")

_last_request = 0.0


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- http
def _throttle():
    global _last_request
    wait = INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def http(url, data=None, headers=None, timeout=30):
    _throttle()
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


# ---------------------------------------------------------------- action id
def discover_action_id():
    """Find the server-action id in the activity page's JS chunk."""
    html = http(PAGE_URL)
    chunks = re.findall(r'src="(/_next/static/chunks/app/[^"]*activity/page-[^"]+\.js)"', html)
    if not chunks:
        raise RuntimeError("activity page chunk not found")
    js = http(BASE + chunks[0])
    m = re.search(r'createServerReference\)\("([0-9a-f]{20,})",[^;]{0,120}?"' + ACTION_NAME + '"', js)
    if not m:
        raise RuntimeError("server action id not found in " + chunks[0])
    return m.group(1)


# ---------------------------------------------------------------- page fetch
def parse_rsc(text):
    for line in text.splitlines():
        m = re.match(r"^[0-9a-f]+:(\{.*\})$", line)
        if not m:
            continue
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "status" in obj:
            if obj.get("status") != "success":
                raise RuntimeError("action returned status=%r" % obj.get("status"))
            return obj["data"]
    raise RuntimeError("no data in response: " + text[:200])


def fetch_page(action_id, page, asset_type=""):
    boundary = "----syfu" + uuid.uuid4().hex
    fields = [("1_page", str(page)), ("1_assetType", asset_type), ("0", '[{},"$K1"]')]
    body = "".join(
        '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v) for k, v in fields
    ) + "--%s--\r\n" % boundary
    headers = {
        "Accept": "text/x-component",
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Next-Action": action_id,
        "Next-Router-State-Tree": ROUTER_STATE,
        "Origin": BASE,
        "Referer": PAGE_URL,
    }
    return parse_rsc(http(PAGE_URL, body.encode("utf-8"), headers))


class Fetcher:
    def __init__(self, state):
        self.state = state
        self.action_id = state.get("action_id")

    def get(self, page):
        failures = 0
        rediscovered = False
        while True:
            try:
                if not self.action_id:
                    self.action_id = discover_action_id()
                    self.state["action_id"] = self.action_id
                    log("action id:", self.action_id)
                return fetch_page(self.action_id, page)
            except (urllib.error.URLError, RuntimeError, TimeoutError, json.JSONDecodeError) as e:
                code = getattr(e, "code", None)
                failures += 1
                log("page %d failed (%s): %s" % (page, code, e))
                if code in (401, 403, 429) and failures >= 2:
                    raise SystemExit("site is refusing requests (%s); stopping" % code)
                # A new site deploy invalidates the action id -> rediscover once.
                if not rediscovered:
                    self.action_id = None
                    rediscovered = True
                if code in (401, 403):
                    raise SystemExit("blocked by site (%s); stopping" % code)
                if failures >= 4:
                    raise SystemExit("too many consecutive failures; stopping")
                time.sleep(30 * 2 ** (failures - 1))


# ---------------------------------------------------------------- storage
def to_row(a):
    usd = re.search(r"([0-9][0-9,]*\.?[0-9]*)", a.get("usdAtSale") or "")
    return {
        "id": a["activityId"],
        "type": a["assetType"],
        "token": a["tokenId"],
        "name": a["name"],
        "img": a.get("imageURL", ""),
        "rarity": a.get("rarity", ""),
        "rank": a.get("parameterRank", ""),
        "lineage": a.get("lineage", ""),
        "origin": a.get("birthProvenance", ""),
        "talent": a.get("talent", ""),
        "level": a.get("levelAtSale", 0),
        "bnb": float(a["amount"]),
        "cur": a.get("currency", "BNB"),
        "usd": float(usd.group(1).replace(",", "")) if usd else None,
        "t": a["soldTime"],
    }


def load_trades():
    trades = {}
    if os.path.exists(TRADES_PATH):
        with open(TRADES_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    trades[r["id"]] = r
    return trades


def save_trades(trades):
    rows = sorted(trades.values(), key=lambda r: (r["t"], r["id"]))
    tmp = TRADES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, TRADES_PATH)


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- modes
def backfill(state, trades, fetcher):
    page = state.get("backfill_next_page", 1)
    total_pages = None
    while True:
        data = fetcher.get(page)
        total = data["totalCount"]
        total_pages = math.ceil(total / PAGE_SIZE)
        for a in data["activities"]:
            trades[a["activityId"]] = to_row(a)
        log("page %d/%d  +%d  (have %d / site %d)" % (page, total_pages, len(data["activities"]), len(trades), total))
        page += 1
        state["backfill_next_page"] = page
        if page % 10 == 0:
            save_trades(trades)
            save_state(state)
        if page > total_pages or not data["activities"]:
            break
    state.pop("backfill_next_page", None)
    state["backfill_done"] = True
    return total


def daily(state, trades, fetcher):
    total = None
    for page in range(1, MAX_DAILY_PAGES + 1):
        data = fetcher.get(page)
        total = data["totalCount"]
        acts = data["activities"]
        known = sum(1 for a in acts if a["activityId"] in trades)
        for a in acts:
            trades[a["activityId"]] = to_row(a)
        log("page %d  %d new, %d known" % (page, len(acts) - known, known))
        if known or not acts:
            break
    return total


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    os.makedirs(DATA_DIR, exist_ok=True)
    state = load_state()
    trades = load_trades()
    before = len(trades)
    if mode == "daily" and not state.get("backfill_done"):
        log("backfill not finished yet -> running backfill")
        mode = "backfill"
    fetcher = Fetcher(state)
    try:
        total = backfill(state, trades, fetcher) if mode == "backfill" else daily(state, trades, fetcher)
    finally:
        save_trades(trades)
        save_state(state)
    state["last_run"] = int(time.time())
    state["site_total"] = total
    state["stored_total"] = len(trades)
    save_state(state)
    log("done: %s  new=%d  stored=%d  site_total=%s" % (mode, len(trades) - before, len(trades), total))
    if total and len(trades) < total:
        log("WARNING: stored fewer trades than the site reports (%d < %d)" % (len(trades), total))


if __name__ == "__main__":
    main()
