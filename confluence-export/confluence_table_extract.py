#!/usr/bin/env python3
"""
Pull table data out of a macro-heavy Confluence page. Try the strategies in order:

  probe       Diagnose the page: which body formats contain tables, which macros are
              on the page, and any ConfiForms export links. Run this FIRST.
  table       Parse a <table> out of body.export_view (or storage/view) -> CSV + JSON.
  confiforms  Download the ConfiForms export (CSV/JSON) -> CSV + JSON.
  pdf         Last resort: FlyingPDF export, optionally extract tables with pdfplumber.

Environment variables:
  CONFLUENCE_BASE    Cloud: https://yourco.atlassian.net/wiki
                     Data Center: https://host/confluence   (no trailing slash)
  CONFLUENCE_USER    Cloud: your Atlassian email (basic auth). Unused for bearer.
  CONFLUENCE_TOKEN   Cloud: API token. Data Center: password or personal access token.
  CONFLUENCE_AUTH    "basic" (default) or "bearer" (Data Center personal access token)
  PAGE_ID            Numeric page ID (or pass --page-id)

Dependencies: requests, beautifulsoup4 (pdfplumber only for `pdf --tables`)
"""
import argparse
import csv
import html as htmllib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = os.environ.get("CONFLUENCE_BASE", "").rstrip("/")
USER = os.environ.get("CONFLUENCE_USER", "")
TOKEN = os.environ.get("CONFLUENCE_TOKEN", "")
AUTH_MODE = os.environ.get("CONFLUENCE_AUTH", "basic").lower()


# ---------- helpers ----------

def make_session():
    if not BASE or not TOKEN:
        sys.exit("Set CONFLUENCE_BASE and CONFLUENCE_TOKEN (and CONFLUENCE_USER for basic auth).")
    s = requests.Session()
    s.headers["X-Atlassian-Token"] = "no-check"
    if AUTH_MODE == "bearer":
        s.headers["Authorization"] = f"Bearer {TOKEN}"
    else:
        s.auth = (USER, TOKEN)
    return s


def get_body(s, page_id, fmt):
    r = s.get(f"{BASE}/rest/api/content/{page_id}",
              params={"expand": f"body.{fmt}"}, timeout=60)
    r.raise_for_status()
    return r.json()["body"][fmt]["value"]


def table_rows(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def dedupe(headers):
    seen, out = Counter(), []
    for h in headers:
        h = h or "col"
        seen[h] += 1
        out.append(h if seen[h] == 1 else f"{h}_{seen[h]}")
    return out


def to_records(rows):
    head = dedupe(rows[0])
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows[1:]]


def write_outputs(records, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(
        json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    if records:
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
            w.writeheader()
            w.writerows(records)
    print(f"Wrote {len(records)} records -> {out_dir / name}.json / .csv")


def candidate_links(soup):
    found = []
    for a in soup.find_all("a", href=True):
        if re.search(r"confiforms|export|\.csv|\.xls|\.json", a["href"], re.I):
            found.append(urljoin(BASE + "/", htmllib.unescape(a["href"])))
    return found


# ---------- commands ----------

def cmd_probe(a):
    s = make_session()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for fmt in ("storage", "export_view", "view"):
        body = get_body(s, a.page_id, fmt)
        (out / f"{fmt}.html").write_text(body, encoding="utf-8")
        soup = BeautifulSoup(body, "html.parser")
        n_tables = len(soup.find_all("table"))
        print(f"\n[{fmt}] {len(body):,} chars, {n_tables} <table> tag(s), "
              f"{len(soup.select('[class*=confi]'))} element(s) with 'confi' in class")
        if fmt == "storage":
            macros = soup.find_all("ac:structured-macro")
            print("  macros on page:", dict(Counter(m.get("ac:name") for m in macros)))
            for m in macros:
                name = m.get("ac:name", "")
                if re.search(r"confi", name, re.I):
                    params = {p.get("ac:name"): p.get_text(strip=True)
                              for p in m.find_all("ac:parameter")}
                    print(f"  ConfiForms macro '{name}' params: {params}")
        else:
            for link in candidate_links(soup):
                print("  candidate export link:", link)
    print(f"\nRaw bodies saved in {out}/ - open export_view.html to see what the table looks like.")


def cmd_table(a):
    s = make_session()
    body = get_body(s, a.page_id, a.source)
    tables = BeautifulSoup(body, "html.parser").find_all("table")
    if a.contains:
        tables = [t for t in tables if a.contains.lower() in t.get_text(" ").lower()]
    if not tables:
        sys.exit(f"No matching table in body.{a.source}. Run `probe`, then try the "
                 f"`confiforms` command or `--source storage`.")
    rows = table_rows(tables[a.index])
    write_outputs(to_records(rows), Path(a.out), "table")


def cmd_confiforms(a):
    s = make_session()
    url = a.url
    if not url:
        body = get_body(s, a.page_id, "export_view")
        links = [l for l in candidate_links(BeautifulSoup(body, "html.parser"))
                 if re.search(r"confiforms", l, re.I)]
        if not links:
            sys.exit("No ConfiForms export link in export_view. Enable 'export' on the "
                     "TableView macro, or click the CSV link in your browser with DevTools "
                     "-> Network open and pass that URL via --url.")
        url = links[0]
        print("Using export link:", url)
    r = s.get(url, timeout=600)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    if text.lstrip().lower().startswith(("<!doctype", "<html")):
        sys.exit("Got an HTML page instead of data (probably a login page or an app that "
                 "needs a browser session). Check auth, or try a different export URL.")
    out = Path(a.out)
    if text.lstrip().startswith(("{", "[")):
        out.mkdir(parents=True, exist_ok=True)
        (out / "confiforms.json").write_text(text, encoding="utf-8")
        print("Saved raw JSON ->", out / "confiforms.json")
    else:
        records = list(csv.DictReader(text.splitlines()))
        write_outputs(records, out, "confiforms")


def cmd_pdf(a):
    s = make_session()
    r = s.get(f"{BASE}/spaces/flyingpdf/pdfpageexport.action",
              params={"pageId": a.page_id}, allow_redirects=False, timeout=300)
    if r.status_code in (301, 302, 303, 307, 308):
        dl = urljoin(BASE + "/", r.headers["Location"])
    else:
        m = re.search(r'href="([^"]+\.pdf[^"]*)"', r.text)
        if not m:
            sys.exit(f"No redirect or PDF link (HTTP {r.status_code}). On Cloud this page may "
                     f"build the link with JavaScript; use the export_view/ConfiForms routes.")
        dl = urljoin(BASE + "/", htmllib.unescape(m.group(1)))
    pdf = s.get(dl, timeout=300)
    pdf.raise_for_status()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "page.pdf"
    path.write_bytes(pdf.content)
    print(f"Saved {len(pdf.content):,} bytes -> {path}")
    if a.tables:
        import pdfplumber  # pip install pdfplumber
        with pdfplumber.open(path) as doc:
            for i, page in enumerate(doc.pages, 1):
                for j, tb in enumerate(page.extract_tables(), 1):
                    p = out / f"pdf_p{i}_t{j}.csv"
                    with open(p, "w", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerows(tb)
                    print("Wrote", p)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--page-id", default=os.environ.get("PAGE_ID"))
    p.add_argument("--out", default="out")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe").set_defaults(fn=cmd_probe)
    t = sub.add_parser("table")
    t.add_argument("--source", default="export_view", choices=["export_view", "view", "storage"])
    t.add_argument("--contains", help="pick the table whose text contains this (e.g. a column header)")
    t.add_argument("--index", type=int, default=0, help="table index after filtering")
    t.set_defaults(fn=cmd_table)
    c = sub.add_parser("confiforms")
    c.add_argument("--url", help="export URL; omit to auto-discover from export_view")
    c.set_defaults(fn=cmd_confiforms)
    d = sub.add_parser("pdf")
    d.add_argument("--tables", action="store_true", help="extract tables with pdfplumber")
    d.set_defaults(fn=cmd_pdf)
    a = p.parse_args()
    if not a.page_id:
        sys.exit("Provide --page-id or set PAGE_ID.")
    a.fn(a)


if __name__ == "__main__":
    main()
