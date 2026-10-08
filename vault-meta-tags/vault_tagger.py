#!/usr/bin/env python3
"""
vault_tagger.py - track expiring credentials in HashiCorp Vault (KV v2)
using custom metadata.

Commands
  plan     DRY RUN. Scan Vault, apply rules.json, write plan.json with the
           recommended tags. Changes nothing in Vault.
  apply    Apply an (edited) plan.json. Supports --dry-run. By default only
           fills in tags that are missing; never overwrites existing tags.
  report   Inventory plus rotation/expiry status, built from the tags.
           Read-only. Good for cron / CI.
  rotated  Record a rotation on one secret (sets last_rotated / next_rotation).

Only the KV v2 *metadata* endpoints are used. Secret values are never read.

Auth: VAULT_ADDR + VAULT_TOKEN (or ~/.vault-token). If you already have an
auth routine (AppRole, OIDC, ...) replace get_client() with it.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import hvac
    from hvac.exceptions import Forbidden, InvalidPath
except ImportError:
    sys.exit("This script needs hvac:  pip install hvac")

TODAY = dt.date.today()

MAX_PAIRS = 64          # Vault KV v2 limit on custom metadata
MAX_KEY_BYTES = 128
MAX_VALUE_BYTES = 512

CONF_RANK = {"low": 0, "medium": 1, "high": 2}
DATE_TAGS = ("last_rotated", "next_rotation", "expiry_date")
UNTRACKED_TYPES = {"config", "ignore"}   # tagged, but nothing to renew
STAMP_NOT_A_CONFLICT = {"tagged_on", "tagged_by", "review_status"}

STATUS_ORDER = {
    "overdue": 0, "due-soon": 1, "bad-date": 2, "no-schedule": 3,
    "untagged": 4, "ok": 5, "not-tracked": 6,
}


def eprint(*a):
    print(*a, file=sys.stderr)


# --------------------------------------------------------------------------
# Vault access
# --------------------------------------------------------------------------
def get_client(args):
    addr = args.addr or os.environ.get("VAULT_ADDR")
    token = os.environ.get("VAULT_TOKEN")
    if not token:
        tf = Path.home() / ".vault-token"
        if tf.exists():
            token = tf.read_text().strip()
    if not addr or not token:
        sys.exit("Set VAULT_ADDR and VAULT_TOKEN (or use --addr / ~/.vault-token).")
    client = hvac.Client(
        url=addr,
        token=token,
        namespace=args.namespace or os.environ.get("VAULT_NAMESPACE"),
        verify=os.environ.get("VAULT_CACERT") or True,
    )
    if not client.is_authenticated():
        sys.exit("Vault authentication failed.")
    return client


def kv2_mounts(client, requested):
    if requested:
        return [m.strip("/") for m in requested]
    try:
        resp = client.sys.list_mounted_secrets_engines()
    except Forbidden:
        sys.exit("Token cannot read sys/mounts. Pass --mount <name> explicitly.")
    mounts = resp.get("data", resp)
    out = []
    for name, info in mounts.items():
        opts = info.get("options") or {}
        if info.get("type") == "kv" and str(opts.get("version")) == "2":
            out.append(name.strip("/"))
    return sorted(out)


def walk(client, mount, prefix=""):
    """Yield every secret path (relative to the mount) under prefix."""
    stack = [prefix]
    while stack:
        p = stack.pop()
        try:
            resp = client.secrets.kv.v2.list_secrets(path=p, mount_point=mount)
        except InvalidPath:
            continue
        except Forbidden:
            eprint(f"  no list permission on {mount}/{p} - skipped")
            continue
        for key in resp["data"]["keys"]:
            if key.endswith("/"):
                stack.append(f"{p}{key}")
            else:
                yield f"{p}{key}"


def fetch_meta(client, mount, path):
    try:
        d = client.secrets.kv.v2.read_secret_metadata(path=path, mount_point=mount)["data"]
    except (InvalidPath, Forbidden) as e:
        return {"mount": mount, "path": path, "error": type(e).__name__}
    return {
        "mount": mount,
        "path": path,
        "custom": dict(d.get("custom_metadata") or {}),
        "created_time": d.get("created_time"),
        "updated_time": d.get("updated_time"),
        "max_versions": d.get("max_versions"),
        "cas_required": d.get("cas_required"),
        "delete_version_after": d.get("delete_version_after"),
    }


def collect(client, args):
    """Return metadata for every secret in scope (mounts/prefix or paths file)."""
    targets = []
    if args.paths_file:
        for line in Path(args.paths_file).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                mount, _, path = line.partition("/")
                targets.append((mount, path))
    else:
        prefix = args.prefix.strip("/")
        prefix = prefix + "/" if prefix else ""
        for mount in kv2_mounts(client, args.mount):
            n = 0
            for path in walk(client, mount, prefix):
                targets.append((mount, path))
                n += 1
            eprint(f"  {mount}/: {n} secrets")
    eprint(f"Reading metadata for {len(targets)} secrets...")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        items = list(ex.map(lambda t: fetch_meta(client, *t), targets))
    bad = [i for i in items if "error" in i]
    if bad:
        eprint(f"  {len(bad)} unreadable (permissions or deleted) - skipped")
    return [i for i in items if "error" not in i]


def write_custom(client, cur, merged):
    """Write custom_metadata, preserving the other metadata settings."""
    kwargs = {}
    if cur.get("max_versions") is not None:
        kwargs["max_versions"] = cur["max_versions"]
    if cur.get("cas_required") is not None:
        kwargs["cas_required"] = cur["cas_required"]
    dva = cur.get("delete_version_after")
    if dva and dva != "0s":
        kwargs["delete_version_after"] = dva
    client.secrets.kv.v2.update_metadata(
        path=cur["path"], mount_point=cur["mount"],
        custom_metadata=merged, **kwargs,
    )
    after = fetch_meta(client, cur["mount"], cur["path"])
    return after.get("custom") == merged


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def parse_date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s).strip())
    except ValueError:
        return None


def as_list(x):
    return x if isinstance(x, list) else [x]


def validate(tags):
    problems = []
    if len(tags) > MAX_PAIRS:
        problems.append(f"more than {MAX_PAIRS} custom metadata pairs")
    for k, v in tags.items():
        if len(k.encode()) > MAX_KEY_BYTES:
            problems.append(f"key too long: {k}")
        if len(str(v).encode()) > MAX_VALUE_BYTES:
            problems.append(f"value too long for {k}")
    for k in DATE_TAGS:
        if k in tags and not parse_date(tags[k]):
            problems.append(f"{k} is not YYYY-MM-DD: {tags[k]!r}")
    return problems


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def print_table(rows, cols):
    if not rows:
        print("(none)")
        return
    widths = [max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------
class _Safe(dict):
    def __missing__(self, key):
        return ""


def load_rules(path):
    rules = json.loads(Path(path).read_text())
    for r in rules.get("rules", []):
        if "name" not in r or "match" not in r:
            sys.exit(f"Every rule needs 'name' and 'match': {r}")
        flags = re.I
        r["_rx"] = [re.compile(x, flags) for x in as_list(r["match"]["path_regex"])]
        r["_ex"] = [re.compile(x, flags) for x in as_list(r["match"].get("exclude_regex", []))]
    return rules


def match_rule(rules, full):
    """First matching rule wins. Matched against '<mount>/<path>'."""
    for r in rules.get("rules", []):
        m = next((m for m in (rx.search(full) for rx in r["_rx"]) if m), None)
        if not m:
            continue
        if any(rx.search(full) for rx in r["_ex"]):
            continue
        return r, m
    return None, None


def render_tags(rule, m, mount, path):
    ctx = _Safe({k: (v or "") for k, v in m.groupdict().items()})
    ctx.update(mount=mount, path=path, basename=path.rsplit("/", 1)[-1])
    tags = {}
    for k, v in rule.get("set", {}).items():
        val = str(v).format_map(ctx).strip()
        if val:
            tags[k] = val
    return tags


def infer_dates(effective, updated_time, to_set):
    """If rotation_days is known, work out last_rotated / next_rotation."""
    days = str(effective.get("rotation_days", ""))
    if not days.isdigit():
        return
    last = parse_date(effective.get("last_rotated"))
    if not last:
        last = parse_date((updated_time or "")[:10])
        if last:
            to_set["last_rotated"] = last.isoformat()
    if last and not effective.get("next_rotation"):
        to_set["next_rotation"] = (last + dt.timedelta(days=int(days))).isoformat()


# --------------------------------------------------------------------------
# plan  (dry run)
# --------------------------------------------------------------------------
def cmd_plan(args):
    rules = load_rules(args.rules)
    defaults = rules.get("defaults", {})
    min_conf = defaults.get("auto_approve_min_confidence", "medium")
    stamps = dict(defaults.get("tags", {}))

    client = get_client(args)
    items = collect(client, args)

    entries, unclassified, skipped, unchanged = [], [], 0, 0
    for it in items:
        mount, path, existing = it["mount"], it["path"], it["custom"]
        full = f"{mount}/{path}"
        rule, m = match_rule(rules, full)

        if rule and rule.get("action") == "skip":
            skipped += 1
            continue
        if rule:
            wanted = render_tags(rule, m, mount, path)
            conf, rname = rule.get("confidence", "medium"), rule["name"]
        elif existing.get("type"):
            wanted, conf, rname = {}, "high", "(existing tags)"
        else:
            unclassified.append(full)
            continue

        to_set = {k: v for k, v in wanted.items() if k not in existing}
        conflicts = {
            k: {"existing": existing[k], "recommended": v}
            for k, v in wanted.items()
            if k in existing and existing[k] != v and k not in STAMP_NOT_A_CONFLICT
        }
        infer_dates({**existing, **to_set}, it.get("updated_time"), to_set)
        if to_set:
            for k, v in stamps.items():
                if k not in existing:
                    to_set.setdefault(k, v)
            if "tagged_on" not in existing:
                to_set.setdefault("tagged_on", TODAY.isoformat())

        problems = validate({**existing, **to_set})
        if problems:
            action = "error"
        elif not to_set:
            action = "none"
        else:
            action = "update" if existing else "add"

        if action == "none" and not conflicts:
            unchanged += 1
            continue

        entries.append({
            "mount": mount,
            "path": path,
            "action": action,
            "rule": rname,
            "confidence": conf,
            "approve": action in ("add", "update")
                       and CONF_RANK.get(conf, 0) >= CONF_RANK[min_conf],
            "existing": existing,
            "set": to_set,
            "conflicts": conflicts,
            "problems": problems,
            "secret_updated_time": it.get("updated_time"),
        })

    entries.sort(key=lambda e: (e["mount"], e["path"]))
    types = Counter((e["set"].get("type") or e["existing"].get("type") or "?")
                    for e in entries if e["action"] in ("add", "update"))
    summary = {
        "scanned": len(items),
        "to_add": sum(e["action"] == "add" for e in entries),
        "to_update": sum(e["action"] == "update" for e in entries),
        "auto_approved": sum(e["approve"] for e in entries),
        "needs_review": sum(e["action"] in ("add", "update") and not e["approve"] for e in entries),
        "with_conflicts": sum(bool(e["conflicts"]) for e in entries),
        "errors": sum(e["action"] == "error" for e in entries),
        "already_tagged_ok": unchanged,
        "skipped_by_rule": skipped,
        "unclassified": len(unclassified),
        "by_type": dict(types),
    }
    plan = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "vault_addr": getattr(client, "url", ""),
        "rules_file": args.rules,
        "summary": summary,
        "entries": entries,
        "unclassified": unclassified,
    }
    Path(args.out).write_text(json.dumps(plan, indent=2))
    if args.csv:
        rows = [{
            "mount": e["mount"], "path": e["path"], "action": e["action"],
            "rule": e["rule"], "confidence": e["confidence"], "approve": e["approve"],
            "set": "; ".join(f"{k}={v}" for k, v in e["set"].items()),
            "conflicts": "; ".join(f"{k}: {v['existing']} -> {v['recommended']}"
                                   for k, v in e["conflicts"].items()),
        } for e in entries]
        write_csv(args.csv, rows, ["mount", "path", "action", "rule", "confidence",
                                   "approve", "set", "conflicts"])

    print("\nDRY RUN - nothing was changed in Vault.\n")
    for k, v in summary.items():
        print(f"  {k:20s} {v}")
    print(f"\nPlan written to {args.out}" + (f" and {args.csv}" if args.csv else ""))
    if unclassified:
        print(f"{len(unclassified)} secrets matched no rule (listed under 'unclassified' "
              "in the plan) - add rules to cover them.")
    print("Next: review the plan, set \"approve\": false on anything you don't want, "
          "then run:  vault_tagger.py apply --plan " + args.out + " --dry-run")
    return 0


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def cmd_apply(args):
    plan = json.loads(Path(args.plan).read_text())
    min_rank = CONF_RANK[args.min_confidence] if args.min_confidence else 0
    only = re.compile(args.only, re.I) if args.only else None

    todo = [
        e for e in plan["entries"]
        if e.get("approve") and e["action"] in ("add", "update")
        and CONF_RANK.get(e.get("confidence", "low"), 0) >= min_rank
        and (not only or only.search(f"{e['mount']}/{e['path']}"))
    ]
    print(f"{len(todo)} secrets selected (of {len(plan['entries'])} entries in plan)\n")
    for e in todo[:25]:
        print(f"  {e['mount']}/{e['path']}  [{e['rule']}, {e['confidence']}]")
        for k, v in e["set"].items():
            print(f"      + {k} = {v}")
    if len(todo) > 25:
        print(f"  ... and {len(todo) - 25} more")
    if not todo:
        return 0
    if args.dry_run:
        print("\nDRY RUN - nothing written.")
        return 0
    if not args.yes and input("\nType 'apply' to write these changes: ").strip() != "apply":
        print("Aborted.")
        return 1

    client = get_client(args)
    log = []
    for e in todo:
        ref = f"{e['mount']}/{e['path']}"
        cur = fetch_meta(client, e["mount"], e["path"])
        if "error" in cur:
            status = f"skipped: {cur['error']}"
        elif cur["custom"] != e["existing"]:
            status = "skipped: metadata changed since the plan was made (re-run plan)"
        else:
            merged = {**cur["custom"], **e["set"]}
            if args.overwrite:
                merged.update({k: c["recommended"] for k, c in e.get("conflicts", {}).items()})
            try:
                ok = write_custom(client, cur, merged)
                status = "written" if ok else "WARNING: written but read-back differs"
            except Exception as ex:  # keep going, report at the end
                status = f"FAILED: {ex}"
        print(f"  {ref}\n      -> {status}")
        log.append({"secret": ref, "status": status, "before": e["existing"], "set": e["set"]})

    logfile = args.log or f"apply_log_{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    Path(logfile).write_text(json.dumps(log, indent=2))
    ok = sum(l["status"] == "written" for l in log)
    print(f"\n{ok}/{len(log)} written. Log (includes 'before' values for rollback): {logfile}")
    return 0 if ok == len(log) else 1


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def build_row(item, warn_days):
    tags = item["custom"]
    t = tags.get("type", "")
    row = {
        "secret": f"{item['mount']}/{item['path']}",
        "type": t or "-",
        "owner": tags.get("owner", "-"),
        "system": tags.get("system", "-"),
        "rotation_method": tags.get("rotation_method", "-"),
        "review_status": tags.get("review_status", "-"),
        "due": "-", "days_left": "", "status": "",
    }
    if not t:
        row["status"] = "untagged"
    elif t in UNTRACKED_TYPES:
        row["status"] = "not-tracked"
    else:
        raw = [tags.get("next_rotation"), tags.get("expiry_date")]
        dates = [d for d in (parse_date(x) for x in raw) if d]
        if not dates:
            row["status"] = "bad-date" if any(raw) else "no-schedule"
        else:
            due = min(dates)
            left = (due - TODAY).days
            row["due"], row["days_left"] = due.isoformat(), left
            row["status"] = "overdue" if left < 0 else "due-soon" if left <= warn_days else "ok"
    return row


def cmd_report(args):
    client = get_client(args)
    items = collect(client, args)
    rows = [build_row(i, args.warn_days) for i in items]

    print("\nSummary (all secrets in scope)")
    print_table(
        [{"status": s, "count": c} for s, c in
         sorted(Counter(r["status"] for r in rows).items(), key=lambda x: STATUS_ORDER[x[0]])],
        ["status", "count"])
    tracked = [r for r in rows if r["status"] not in ("untagged", "not-tracked")]
    print("\nTracked credentials by type")
    print_table(
        [{"type": t, "count": c} for t, c in sorted(Counter(r["type"] for r in tracked).items())],
        ["type", "count"])

    shown = rows
    if not args.all:
        shown = [r for r in rows if r["status"] not in ("untagged", "not-tracked")]
    if args.type:
        shown = [r for r in shown if r["type"] == args.type]
    if args.owner:
        shown = [r for r in shown if r["owner"] == args.owner]
    if args.status:
        shown = [r for r in shown if r["status"] == args.status]
    shown.sort(key=lambda r: (STATUS_ORDER[r["status"]],
                              r["days_left"] if r["days_left"] != "" else 10 ** 9,
                              r["secret"]))

    cols = ["status", "days_left", "due", "type", "owner", "system", "rotation_method", "secret"]
    print(f"\nDetail ({len(shown)} rows; use --all to include untagged/not-tracked)")
    print_table(shown, cols)
    if args.csv:
        write_csv(args.csv, shown, ["secret", "type", "owner", "system", "rotation_method",
                                    "review_status", "due", "days_left", "status"])
        print(f"\nCSV written to {args.csv}")
    if args.json:
        Path(args.json).write_text(json.dumps(shown, indent=2))
        print(f"JSON written to {args.json}")
    if args.fail_on_overdue and any(r["status"] == "overdue" for r in rows):
        return 2
    return 0


# --------------------------------------------------------------------------
# rotated
# --------------------------------------------------------------------------
def cmd_rotated(args):
    client = get_client(args)
    mount, path = args.mount.strip("/"), args.path.strip("/")
    cur = fetch_meta(client, mount, path)
    if "error" in cur:
        sys.exit(f"Cannot read metadata for {mount}/{path}: {cur['error']}")
    days = args.rotation_days or cur["custom"].get("rotation_days")
    if not days or not str(days).isdigit():
        sys.exit("No rotation_days tag on this secret - pass --rotation-days N.")
    when = parse_date(args.date) if args.date else TODAY
    if not when:
        sys.exit("--date must be YYYY-MM-DD")
    new = {
        "rotation_days": str(days),
        "last_rotated": when.isoformat(),
        "next_rotation": (when + dt.timedelta(days=int(days))).isoformat(),
    }
    if args.expiry_date:
        if not parse_date(args.expiry_date):
            sys.exit("--expiry-date must be YYYY-MM-DD")
        new["expiry_date"] = args.expiry_date
    merged = {**cur["custom"], **new}
    problems = validate(merged)
    if problems:
        sys.exit("Refusing to write: " + "; ".join(problems))
    for k, v in new.items():
        print(f"  {k}: {cur['custom'].get(k, '(unset)')} -> {v}")
    if args.dry_run:
        print("DRY RUN - nothing written.")
        return 0
    ok = write_custom(client, cur, merged)
    print(f"Updated {mount}/{path}" if ok else "WARNING: written but read-back differs")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    conn = argparse.ArgumentParser(add_help=False)
    conn.add_argument("--addr", help="Vault address (default: $VAULT_ADDR)")
    conn.add_argument("--namespace", help="Vault Enterprise namespace")

    scope = argparse.ArgumentParser(add_help=False)
    scope.add_argument("--mount", action="append",
                       help="KV v2 mount to scan (repeatable). Default: all KV v2 mounts")
    scope.add_argument("--prefix", default="", help="Only scan under this path prefix")
    scope.add_argument("--paths-file",
                       help="Text file of '<mount>/<path>' lines (e.g. from your existing "
                            "report) to use instead of walking Vault")
    scope.add_argument("--workers", type=int, default=8, help="Parallel metadata reads")

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", parents=[conn, scope], help="Dry run: recommend tags")
    sp.add_argument("--rules", default="rules.json")
    sp.add_argument("--out", default="plan.json")
    sp.add_argument("--csv", help="Also write the plan as CSV for spreadsheet review")
    sp.set_defaults(fn=cmd_plan)

    sp = sub.add_parser("apply", parents=[conn], help="Apply an approved plan")
    sp.add_argument("--plan", default="plan.json")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    sp.add_argument("--min-confidence", choices=list(CONF_RANK))
    sp.add_argument("--only", help="Regex: only apply entries whose mount/path matches")
    sp.add_argument("--overwrite", action="store_true",
                    help="Also replace existing tags that differ from the recommendation")
    sp.add_argument("--log", help="Where to write the apply log")
    sp.set_defaults(fn=cmd_apply)

    sp = sub.add_parser("report", parents=[conn, scope], help="Rotation / expiry report")
    sp.add_argument("--warn-days", type=int, default=30)
    sp.add_argument("--type")
    sp.add_argument("--owner")
    sp.add_argument("--status", choices=list(STATUS_ORDER))
    sp.add_argument("--all", action="store_true", help="Include untagged and not-tracked rows")
    sp.add_argument("--csv")
    sp.add_argument("--json")
    sp.add_argument("--fail-on-overdue", action="store_true",
                    help="Exit code 2 if anything is overdue (for cron / CI alerts)")
    sp.set_defaults(fn=cmd_report)

    sp = sub.add_parser("rotated", parents=[conn], help="Record a rotation on one secret")
    sp.add_argument("--mount", required=True)
    sp.add_argument("--path", required=True)
    sp.add_argument("--date", help="Rotation date YYYY-MM-DD (default: today)")
    sp.add_argument("--rotation-days", type=int)
    sp.add_argument("--expiry-date", help="Hard expiry YYYY-MM-DD (certs, tokens)")
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_rotated)

    args = p.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
