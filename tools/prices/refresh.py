"""Refresh prices of every mapped store product. GitHub Actions runs it weekly in the public prices repo
mm7mm1/cook_app_prices (same file layout, filled by tools/prices/publish.py); it also works locally.

  python3 tools/prices/refresh.py

data/prices/prices.json: {"updated": iso, "items": {"silpo:<ref>": {"price_uah", "in_stock", "at"[, "gone"]}}}
"""
import json
import os
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stores  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MAPPING = ROOT / "data" / "store_products.json"
PRICES = ROOT / "data" / "prices" / "prices.json"
# Status codes a store answers for a product it no longer lists (Novus gives 400 for an unknown EAN).
GONE = {"silpo": {404}, "novus": {400, 404}}
RETRIES = 3
# More delisted products than this means the API changed, not the assortment.
MAX_GONE_SHARE = 0.1


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def refs(mapping):
    return sorted({(store, p["ref"]) for entry in mapping.values() for store, products in entry.items() for p in products})


def fetch_store(store, todo, stop, fetch=stores.fetch, pause=2):
    """One store at a time per thread to stay polite; the first hard failure stops both stores."""
    out = {}
    for ref in todo:
        if stop.is_set():
            break
        for attempt in range(RETRIES):
            try:
                out[ref] = fetch(store, ref)
                break
            except urllib.error.HTTPError as e:
                if e.code in GONE[store]:
                    out[ref] = None
                    break
                err = e
            except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as e:
                err = e
            time.sleep(pause * 2 ** attempt)
        else:
            stop.set()
            raise RuntimeError(f"{store}:{ref} failed {RETRIES} times, prices untouched: {err!r}")
    return out


def build(mapping, old, fetched, now):
    """A delisted product keeps its last known price, so the app can still estimate the cost and warn."""
    items, gone = {}, []
    keys = refs(mapping)
    for store, ref in keys:
        key, p = f"{store}:{ref}", fetched[store][ref]
        if p is None:
            gone.append(key)
            if key in old.get("items", {}):
                items[key] = {**old["items"][key], "in_stock": False, "gone": True}
            continue
        items[key] = {"price_uah": p["price_uah"], "in_stock": p["in_stock"], "at": now}
    if len(gone) > MAX_GONE_SHARE * len(keys):
        raise RuntimeError(f"{len(gone)} of {len(keys)} products gone, looks like an API change; prices untouched")
    return {"updated": now, "items": items}, gone


def main():
    mapping = load(MAPPING)
    old = load(PRICES) if PRICES.exists() else {"items": {}}
    todo = {}
    for store, ref in refs(mapping):
        todo.setdefault(store, []).append(ref)
    stop = threading.Event()
    with ThreadPoolExecutor(len(todo)) as pool:
        futures = {store: pool.submit(fetch_store, store, r, stop) for store, r in todo.items()}
    fetched = {store: f.result() for store, f in futures.items()}
    prices, gone = build(mapping, old, fetched, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    dump(PRICES, prices)
    out = sum(1 for v in prices["items"].values() if not v["in_stock"])
    print(f"prices refreshed: {len(prices['items'])} products, out of stock {out}, gone {len(gone)} {gone[:10]}")


if __name__ == "__main__":
    main()
