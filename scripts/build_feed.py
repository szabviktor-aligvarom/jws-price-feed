#!/usr/bin/env python3
"""
Napi ar- es keszletfeed a jws-store.de oldalarol.

Adatforras: sitemap.xml -> termekoldalak -> JSON-LD + beagyazott Ceres termek-JSON.
(A shop Plentymarkets alapu, nincs publikus termek-API, ezert strukturalt
adatot olvasunk a termekoldalakrol, nem nyers HTML-t.)

A script SOSEM ir felul jo adatot, ha a sanity-check megbukik: ilyenkor
nem-nulla exit koddal leall, es a repoban marad az utolso jo feed.
"""
import re
import os
import io
import csv
import sys
import gzip
import json
import time
import random
import datetime
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

BASE = "https://www.jws-store.de"
SITEMAP = BASE + "/sitemap/Sitemap_item.xml"
CURRENCY = "EUR"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED_DIR = os.path.join(ROOT, "feed")
JSON_PATH = os.path.join(FEED_DIR, "feed.json")
CSV_PATH = os.path.join(FEED_DIR, "feed.csv")
STATE_PATH = os.path.join(FEED_DIR, "state.json")
REPORT_PATH = os.path.join(FEED_DIR, "last_run.json")

# ---------------- sanity-check kuszobok ----------------
MIN_PRODUCTS = 1200          # fix minimum termekszam
MAX_MISSING_PRICE_PCT = 20.0  # max ennyi %-nal lehet hianyzo ar
MAX_SHRINK_PCT = 30.0         # katalogus max ennyit zuhanhat az elozo futashoz kepest
MAX_AVG_PRICE_DRIFT_PCT = 30.0  # atlagar max ennyit mozdulhat (formatum-hiba elleni ovo)
MAX_FETCH_FAIL_PCT = 10.0     # max ennyi %-a bukhat el a letolteseknek
MAX_GONE_PCT = 15.0          # max ennyi %-a lehet 404 (URL-szerkezet valtozas elleni ovo)

WORKERS = int(os.environ.get("FEED_WORKERS", "8"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# A shop sajat keszletjelzese alapjan dontunk. A Plentymarkets "isSalable"
# flag ezen a shopon megbizhatatlan (rendelheto termeket is false-nak jelol),
# ezert kizarolag a szoveges jelzest hasznaljuk.
OUT_OF_STOCK_MARKERS = ("vergriffen", "ausverkauft", "nicht verf")


def log(msg):
    print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def fetch(url, tries=4):
    """A visszateres (html, status). A 404 vegleges: torolt termek, nem hiba,
    ezert nem probaljuk ujra, es nem szamit halozati hibanak."""
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
                "Accept-Language": "de-DE,de;q=0.9",
                "Accept-Encoding": "gzip",
            })
            with urllib.request.urlopen(req, timeout=45) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", "replace"), 200
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return None, e.code
            if t < tries - 1:
                time.sleep(1.5 * (t + 1) + random.random())
        except Exception:
            if t < tries - 1:
                time.sleep(1.5 * (t + 1) + random.random())
    return None, 0


def balanced(s, i):
    """A JSON objektum kinyerese s[i]=='{' pozíciotol, string-tudatosan."""
    depth = 0
    instr = False
    esc = False
    for j in range(i, len(s)):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i:j + 1]
    return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_product(url, h):
    out = {"url": url}

    # ---- JSON-LD Product ----
    prod = None
    for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try:
            d = json.loads(m)
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            prod = d
        elif isinstance(d, dict) and d.get("@type") == "BreadcrumbList":
            names = [e.get("item", {}).get("name", "") for e in d.get("itemListElement", [])]
            out["category"] = " > ".join(n.strip() for n in names[1:-1] if n)

    list_price = 0.0
    sale_price = 0.0
    if prod:
        out["name"] = (prod.get("name") or "").strip()
        out["image_url"] = prod.get("image") or ""
        out["brand"] = (prod.get("manufacturer") or {}).get("name", "")
        out["item_id"] = str(prod.get("identifier") or "")
        if not out.get("category"):
            out["category"] = prod.get("category") or ""
        w = prod.get("weight") or {}
        out["weight_g"] = w.get("value", "") if isinstance(w, dict) else ""
        off = prod.get("offers") or {}
        if isinstance(off, list):
            off = off[0] if off else {}
        sale_price = to_float(off.get("price"))
        for spec in (off.get("priceSpecification") or []):
            if not isinstance(spec, dict):
                continue
            pt = str(spec.get("priceType") or "")
            if pt.endswith("ListPrice"):
                list_price = to_float(spec.get("price"))
            elif pt.endswith("SalePrice"):
                sale_price = sale_price or to_float(spec.get("price"))

    # ---- beagyazott Ceres variacio: cikkszam + keszletjelzes ----
    m = re.search(r'"variation":\{"position"', h)
    if m:
        blob = balanced(h, m.start() + len('"variation":'))
        if blob:
            try:
                v = json.loads(blob)
                out["sku"] = str(v.get("number") or "").strip()
                out["variation_id"] = v.get("id") or ""
                out["model"] = v.get("model") or ""
                if not out.get("weight_g"):
                    out["weight_g"] = v.get("weightG") or ""
                av = v.get("availability") or {}
                nm = av.get("names") or {}
                out["availability_text"] = (nm.get("name") or av.get("name") or "").strip()
                out["delivery_days"] = av.get("averageDays", "")
            except Exception:
                pass

    # ---- ar: a beagyazott ar a megbizhatobb (a JSON-LD variansoknal 0-t adhat) ----
    m = re.search(r'"prices":\{"default":\{"price":\{"value":([\d.]+)', h)
    embedded = to_float(m.group(1)) if m else 0.0
    price = sale_price or embedded
    if embedded > 0 and sale_price > 0 and abs(embedded - sale_price) > 0.01:
        # ha elternek, a beagyazott (variansra vonatkozo) ar nyer
        price = embedded
    if not price:
        price = embedded or sale_price

    out["price"] = round(price, 2) if price else 0.0
    out["list_price"] = round(list_price, 2) if list_price else 0.0
    # akcio csak akkor, ha a listaar tenylegesen magasabb
    if out["list_price"] > out["price"] > 0:
        out["on_sale"] = True
        out["discount_percent"] = round(
            (out["list_price"] - out["price"]) / out["list_price"] * 100, 2)
    else:
        out["on_sale"] = False
        out["discount_percent"] = 0.0
        out["list_price"] = out["list_price"] or out["price"]

    # ---- keszlet: a shop szoveges jelzese alapjan ----
    txt = (out.get("availability_text") or "").lower()
    if not txt:
        out["in_stock"] = False
    else:
        out["in_stock"] = not any(k in txt for k in OUT_OF_STOCK_MARKERS)

    # cikkszam vegso mentesve az URL-bol
    if not out.get("sku"):
        m = re.search(r"_(\d+)_(\d+)$", url)
        if m:
            out["sku"] = m.group(1)
    return out


def job(url):
    h, status = fetch(url)
    if h is None:
        if status in (404, 410):
            # Torolt termek, ami a sitemapban maradt. Nem halozati hiba.
            return {"url": url, "_gone": True}
        return {"url": url, "_fetch_failed": True}
    try:
        return parse_product(url, h)
    except Exception as e:
        return {"url": url, "_parse_error": str(e), "_fetch_failed": True}


def get_urls():
    log("sitemap letoltese")
    xml, _ = fetch(SITEMAP)
    if not xml:
        die("A sitemap nem toltheto le: " + SITEMAP)
    urls = re.findall(r"<loc>(https://www\.jws-store\.de/[^<]+)</loc>", xml)
    urls = [u for u in urls if not re.search(r"\.(jpg|jpeg|png|webp|gif)$", u, re.I)]
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    log("sitemap: %d termek URL" % len(out))
    return out


def die(msg, report=None):
    log("HIBA: " + msg)
    log("A meglevo feed valtozatlan marad.")
    if report is not None:
        report["ok"] = False
        report["error"] = msg
        try:
            os.makedirs(FEED_DIR, exist_ok=True)
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    sys.exit(1)


def dedupe(rows):
    """Egy cikkszam egyszer szerepelhet. Keszleten levo valtozat nyer,
    utana a dragabb (a teljes csomag jellemzoen az). Jelezzuk a duplikaciot."""
    by_sku = {}
    for r in rows:
        by_sku.setdefault(r["sku"], []).append(r)
    out = []
    dup_count = 0
    for sku, group in by_sku.items():
        if len(group) == 1:
            g = group[0]
            g["was_duplicate"] = False
            g["duplicate_count"] = 1
            out.append(g)
            continue
        dup_count += 1
        group.sort(key=lambda r: (0 if r["in_stock"] else 1, -r["price"]))
        keep = group[0]
        keep["was_duplicate"] = True
        keep["duplicate_count"] = len(group)
        out.append(keep)
    log("duplikalt cikkszamok: %d" % dup_count)
    return out, dup_count


FIELDS = [
    "sku", "name", "price", "list_price", "on_sale", "discount_percent",
    "in_stock", "stock_type", "availability_text", "delivery_days",
    "weight_g", "url", "image_url", "category", "brand",
    "was_duplicate", "duplicate_count", "last_modified",
]


def main():
    started = datetime.datetime.now(datetime.timezone.utc)
    report = {
        "started_utc": started.isoformat(timespec="seconds"),
        "source": BASE,
    }
    os.makedirs(FEED_DIR, exist_ok=True)

    prev = {}
    if os.path.exists(STATE_PATH):
        try:
            prev = json.load(open(STATE_PATH, encoding="utf-8"))
        except Exception:
            prev = {}

    urls = get_urls()
    if len(urls) < MIN_PRODUCTS:
        die("A sitemap csak %d terméket ad, a minimum %d. Valoszinuleg a forras hibas."
            % (len(urls), MIN_PRODUCTS), report)

    log("termekoldalak letoltese (%d szal)" % WORKERS)
    raw = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for r in ex.map(job, urls):
            raw.append(r)
            done += 1
            if done % 200 == 0:
                log("  %d/%d" % (done, len(urls)))

    failed = [r for r in raw if r.get("_fetch_failed")]
    gone = [r for r in raw if r.get("_gone")]
    fail_pct = len(failed) / len(raw) * 100 if raw else 100
    gone_pct = len(gone) / len(raw) * 100 if raw else 0
    report["gone_404"] = len(gone)
    report["gone_404_pct"] = round(gone_pct, 2)
    log("letoltes kesz. hibas: %d (%.1f%%), torolt/404: %d (%.1f%%)"
        % (len(failed), fail_pct, len(gone), gone_pct))
    if fail_pct > MAX_FETCH_FAIL_PCT:
        die("A letoltesek %.1f%%-a elbukott (max %.1f%%). Halozati vagy blokkolasi hiba."
            % (fail_pct, MAX_FETCH_FAIL_PCT), report)
    if gone_pct > MAX_GONE_PCT:
        die("A termekoldalak %.1f%%-a 404 (max %.1f%%). Valoszinuleg URL-szerkezet "
            "valtozas a shopban, nem tenyleges termektorles."
            % (gone_pct, MAX_GONE_PCT), report)

    rows = []
    ts = started.isoformat(timespec="seconds")
    for r in raw:
        if r.get("_fetch_failed") or r.get("_gone"):
            continue
        if not r.get("sku") or not r.get("name"):
            continue
        rows.append({
            "sku": r.get("sku", ""),
            "name": r.get("name", ""),
            "price": r.get("price", 0.0),
            "list_price": r.get("list_price", 0.0),
            "on_sale": bool(r.get("on_sale")),
            "discount_percent": r.get("discount_percent", 0.0),
            "in_stock": bool(r.get("in_stock")),
            "stock_type": "boolean",
            "availability_text": r.get("availability_text", ""),
            "delivery_days": r.get("delivery_days", ""),
            "weight_g": r.get("weight_g", ""),
            "url": r.get("url", ""),
            "image_url": r.get("image_url", ""),
            "category": r.get("category", ""),
            "brand": r.get("brand", ""),
            "last_modified": ts,
        })

    if not rows:
        die("Egyetlen termeksor sem allt ossze.", report)

    rows, dup_count = dedupe(rows)
    rows.sort(key=lambda r: (r["category"], r["name"]))

    # ---- 0 aras sorok kulonvalasztasa ----
    # A shop nehany (jellemzoen variansos) terméknél 0,00 EUR-t hirdet, es ez
    # naprol napra valtozhat. Ezek NEM kerulnek a fo feedbe, mert betoltve
    # ingyen adnak ki termeket. Kulon fajlba mennek, hogy latszodjanak.
    zero_rows = [r for r in rows if not r["price"]]
    rows = [r for r in rows if r["price"]]
    log("0 aras sorok kizarva a fo feedbol: %d" % len(zero_rows))

    # ---------------- sanity-checkek ----------------
    total = len(rows)
    missing_price = len(zero_rows)
    missing_pct = missing_price / (total + missing_price) * 100 if (total + missing_price) else 0
    in_stock = sum(1 for r in rows if r["in_stock"])
    priced = [r["price"] for r in rows if r["price"] > 0]
    avg_price = sum(priced) / len(priced) if priced else 0.0

    report.update({
        "products": total,
        "in_stock": in_stock,
        "out_of_stock": total - in_stock,
        "excluded_zero_price": missing_price,
        "excluded_zero_price_pct": round(missing_pct, 2),
        "duplicate_skus": dup_count,
        "avg_price": round(avg_price, 2),
        "fetch_failed": len(failed),
        "currency": CURRENCY,
    })

    log("ellenorzes: %d termek a feedben, %d keszleten, %d kizart 0 aras (%.1f%%), atlagar %.2f"
        % (total, in_stock, missing_price, missing_pct, avg_price))

    if total < MIN_PRODUCTS:
        die("Csak %d termek allt ossze, a minimum %d." % (total, MIN_PRODUCTS), report)

    if missing_pct > MAX_MISSING_PRICE_PCT:
        die("A termekek %.1f%%-anal nincs ar (max %.1f%%)."
            % (missing_pct, MAX_MISSING_PRICE_PCT), report)
    prev_total = prev.get("products") or 0
    if prev_total:
        shrink = (prev_total - total) / prev_total * 100
        report["catalog_change_pct"] = round(-shrink, 2)
        if shrink > MAX_SHRINK_PCT:
            die("A katalogus %.1f%%-ot zuhant (%d -> %d), a max %.1f%%."
                % (shrink, prev_total, total, MAX_SHRINK_PCT), report)

    prev_avg = prev.get("avg_price") or 0
    if prev_avg and avg_price:
        drift = abs(avg_price - prev_avg) / prev_avg * 100
        report["avg_price_drift_pct"] = round(drift, 2)
        if drift > MAX_AVG_PRICE_DRIFT_PCT:
            die("Az atlagar %.1f%%-ot mozdult (%.2f -> %.2f), a max %.1f%%. "
                "Valoszinuleg arformatum-hiba a forrasnal."
                % (drift, prev_avg, avg_price, MAX_AVG_PRICE_DRIFT_PCT), report)

    # ---------------- kiiras ----------------
    generated = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "meta": {
            "source": BASE,
            "currency": CURRENCY,
            "price_type": "gross_retail",
            "price_note": "Brutto kiskereskedelmi arak, a shop nyilvanos arai (inkl. MwSt.), szallitasi koltseg nelkul.",
            "stock_type": "boolean",
            "stock_note": "A shop nem publikal darabszamot, csak keszletjelzest, ezert az in_stock igaz/hamis.",
            "list_price_note": "A list_price a shop athuzott ara, ami tipikusan UVP "
                               "(gyartoi ajanlott fogyasztoi ar), nem korabbi shop-ar. "
                               "Ahol a shop nem ad athuzott arat, ott list_price = price es on_sale = false.",
            "generated_utc": generated.isoformat(timespec="seconds"),
            "generated_budapest": datetime.datetime.now().isoformat(timespec="seconds"),
            "product_count": total,
            "in_stock_count": in_stock,
            "excluded_zero_price_count": len(zero_rows),
            "excluded_note": "A 0,00 EUR-t hirdeto sorok nem szerepelnek itt "
                             "(a shop hibas termekadata miatt betoltve ingyen adnanak ki termeket). "
                             "Ezek a feed/excluded_zero_price.json fajlban lathatok.",
            "duplicate_skus_collapsed": dup_count,
            "identifier": "sku",
        },
        "products": rows,
    }

    tmp_json = JSON_PATH + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    # csak sikeres, teljes kiiras utan cserelunk
    os.replace(tmp_json, JSON_PATH)

    tmp_csv = CSV_PATH + ".tmp"
    with open(tmp_csv, "w", newline="", encoding="utf-8-sig") as f:
        f.write("# jws-store.de ar- es keszletfeed; penznem: %s; "
                "arak: brutto kiskereskedelmi; keszlet: igaz/hamis (nincs darabszam); "
                "generalva (UTC): %s\n" % (CURRENCY, generated.isoformat(timespec="seconds")))
        w = csv.DictWriter(f, fieldnames=FIELDS, delimiter=";")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp_csv, CSV_PATH)

    # a kizart 0 aras sorok kulon, hogy a shop tudja javitani
    with open(os.path.join(FEED_DIR, "excluded_zero_price.json"), "w", encoding="utf-8") as f:
        json.dump({
            "note": "Ezeket a sorokat a shop 0,00 EUR-ral hirdeti, ezert nem kerultek a fo feedbe.",
            "generated_utc": generated.isoformat(timespec="seconds"),
            "count": len(zero_rows),
            "products": zero_rows,
        }, f, ensure_ascii=False, indent=1)

    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "products": total,
            "in_stock": in_stock,
            "avg_price": round(avg_price, 2),
            "generated_utc": generated.isoformat(timespec="seconds"),
        }, f, ensure_ascii=False, indent=1)

    report["ok"] = True
    report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    log("KESZ: %d termek kiirva (%d keszleten)" % (total, in_stock))


if __name__ == "__main__":
    main()
