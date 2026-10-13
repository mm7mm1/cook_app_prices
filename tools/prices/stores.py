"""Silpo and Novus (zakaz.ua) clients normalised to one product shape.

Product: {"store", "ref", "title", "price_uah", "weighted", "pack", "in_stock"}
  ref      - Silpo product id / Novus EAN, enough to fetch the price again
  weighted - sold by weight, price_uah is per kg and any amount can be bought
  pack     - {"amount": float, "unit": "g"|"ml"|"pcs"} for packaged goods, None if unknown
"""
import json
import re
import time
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"
SILPO = "https://sf-ecom-api.silpo.ua/v1/uk/branches/00000000-0000-0000-0000-000000000000/products"
NOVUS = "https://stores-api.zakaz.ua/stores/48201070/products"
DELAY = 0.25
PACK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|г|л|мл|шт)\b", re.I)
TO_BASE = {"кг": (1000, "g"), "г": (1, "g"), "л": (1000, "ml"), "мл": (1, "ml"), "шт": (1, "pcs")}


def _get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "uk", **(headers or {})})
    time.sleep(DELAY)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_pack(text):
    """'0,85л' -> 850 ml, '10шт' -> 10 pcs; the last match wins because titles end with the pack size."""
    found = PACK_RE.findall(text or "")
    if not found:
        return None
    num, unit = found[-1]
    mult, base = TO_BASE[unit.lower()]
    return {"amount": round(float(num.replace(",", ".")) * mult, 1), "unit": base}


def _silpo(item):
    weighted = item.get("ratio") == "кг"
    return {"store": "silpo", "ref": item["id"], "title": item["title"], "price_uah": float(item["price"]),
            "weighted": weighted, "pack": None if weighted else parse_pack(item.get("displayRatio")),
            "in_stock": (item.get("stock") or 0) > 0}


def _novus(item):
    weighted = item.get("unit") == "kg"
    pack = None
    if not weighted:
        # Novus "weight" is gross (jar included), so the net amount from the title wins.
        pack = parse_pack(item["title"])
        if not pack and item.get("volume"):
            pack = {"amount": float(item["volume"]), "unit": "ml"}
        elif not pack and item.get("weight"):
            pack = {"amount": float(item["weight"]), "unit": "g"}
    return {"store": "novus", "ref": item["ean"], "title": item["title"], "price_uah": item["price"] / 100,
            "weighted": weighted, "pack": pack, "in_stock": bool(item.get("in_stock"))}


def _stems(text):
    # Crude Ukrainian stemming: "морква" and "моркву" share "морк", "яйця" and "яйце" share "яйц".
    return [w[:max(3, len(w) - 2)] for w in re.findall(r"[а-яіїєґ'a-z]+", text.lower()) if len(w) > 2]


def rank(query, products, limit):
    """Store search ranks by popularity, so a one-word query returns cat food before carrots."""
    q = _stems(query)
    scored = []
    for n, p in enumerate(products):
        t = p["title"].lower()
        hit = sum(1 for s in q if s in t)
        if hit:
            scored.append((-(hit / len(q)) - (0.5 if q and t.startswith(q[0]) else 0), n, p))
    return [p for *_, p in sorted(scored)[:limit]]


def search(store, query, limit=8):
    q = urllib.parse.quote(query)
    if store == "silpo":
        found = [_silpo(i) for i in _get(f"{SILPO}?limit=20&offset=0&search={q}")["items"]]
    else:
        found = [_novus(i) for i in _get(f"{NOVUS}/search/?q={q}&per_page=20")["results"]]
    return rank(query, found, limit)


def fetch(store, ref):
    if store == "silpo":
        return _silpo(_get(f"{SILPO}/{ref}"))
    return _novus(_get(f"{NOVUS}/{ref}/")["product"])
