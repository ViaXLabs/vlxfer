#!/usr/bin/env python3
"""
vault_match.py - Match service accounts against vault key names.

For every service account name in the service account sheet, look for it in
the "Key Names" column of the vault sheet. Every vault hit becomes one row in
the output: service account name, vault, path, created date, notes.

If one service account matches several vault rows, ALL of them are written and
each is flagged "Duplicate record" in the Notes column.

Inputs can be .csv, .xlsx or .xls (mix and match is fine).

Example:
    python vault_match.py service_accounts.xlsx vault_export.csv -o matches.csv

If your column headers differ from the defaults below, override them:
    python vault_match.py sa.csv vault.csv \
        --sa-name-col "Account Name" \
        --vault-key-col "Secret Name"

Requires: pandas  (and openpyxl for .xlsx, xlrd for old .xls)
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Default column headers (matching is case-insensitive and ignores extra spaces)
# ---------------------------------------------------------------------------
DEFAULT_SA_NAME_COL = "Service Account Name"
DEFAULT_VAULT_COL = "Vault"
DEFAULT_PATH_COL = "Path"
DEFAULT_KEY_COL = "Key Names"
DEFAULT_DATE_COL = "Created Date"


def load_table(path: str) -> pd.DataFrame:
    """Read a CSV or Excel file with everything as text (no surprise conversions)."""
    p = Path(path)
    if not p.exists():
        sys.exit(f"ERROR: file not found: {path}")

    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        df = pd.read_excel(p, dtype=str, keep_default_na=False)
    else:
        # utf-8-sig strips the BOM that Excel adds to CSV exports
        df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")

    df.columns = [str(c).strip() for c in df.columns]
    return df


def find_column(df: pd.DataFrame, wanted: str, label: str) -> str:
    """Find a column by name, ignoring case and extra whitespace."""
    lookup = {" ".join(c.lower().split()): c for c in df.columns}
    key = " ".join(wanted.lower().split())
    if key in lookup:
        return lookup[key]
    sys.exit(
        f"ERROR: could not find the {label} column '{wanted}'.\n"
        f"Columns available: {list(df.columns)}\n"
        f"Use the matching --*-col option to point at the right one."
    )


def norm(value: str) -> str:
    """Normalize text for comparison: trim, collapse spaces, lowercase."""
    return " ".join(str(value).split()).lower()


def build_matches(sa_df, vault_df, cols, match_mode, include_unmatched):
    sa_col = cols["sa_name"]
    vault_col, path_col, key_col, date_col = (
        cols["vault"], cols["path"], cols["key"], cols["date"],
    )

    # Pre-normalize the vault key names once
    vault_df = vault_df.copy()
    vault_df["_key_norm"] = vault_df[key_col].map(norm)

    # Count how often each service account appears in the service account sheet
    sa_df = sa_df.copy()
    sa_df["_name_norm"] = sa_df[sa_col].map(norm)
    sa_counts = sa_df["_name_norm"].value_counts().to_dict()

    # Process each distinct service account once (keep original spelling of first)
    unique_accounts = (
        sa_df[sa_df["_name_norm"] != ""]
        .drop_duplicates("_name_norm")[[sa_col, "_name_norm"]]
    )

    # For exact matching, a dict lookup is much faster than scanning every time
    exact_index = {}
    if match_mode == "exact":
        for idx, k in vault_df["_key_norm"].items():
            exact_index.setdefault(k, []).append(idx)

    rows = []
    for _, acct in unique_accounts.iterrows():
        name, name_norm = acct[sa_col], acct["_name_norm"]

        if match_mode == "exact":
            hit_idx = exact_index.get(name_norm, [])
        else:  # "contains": account name appears anywhere inside the key name
            mask = vault_df["_key_norm"].str.contains(name_norm, regex=False)
            hit_idx = vault_df.index[mask].tolist()

        sa_dupes = sa_counts.get(name_norm, 1)

        if not hit_idx:
            if include_unmatched:
                note = "No match in vault"
                if sa_dupes > 1:
                    note += f"; name listed {sa_dupes}x in service account sheet"
                rows.append({
                    "Service Account Name": name,
                    "Vault": "", "Path": "", "Created Date": "",
                    "Notes": note,
                })
            continue

        for idx in hit_idx:
            v = vault_df.loc[idx]
            notes = []
            if len(hit_idx) > 1:
                notes.append(f"Duplicate record ({len(hit_idx)} vault matches)")
            if match_mode == "contains" and v["_key_norm"] != name_norm:
                notes.append(f"Partial match on key name: {v[key_col]}")
            if sa_dupes > 1:
                notes.append(f"name listed {sa_dupes}x in service account sheet")
            rows.append({
                "Service Account Name": name,
                "Vault": v[vault_col],
                "Path": v[path_col],
                "Created Date": v[date_col],
                "Notes": "; ".join(notes),
            })

    return pd.DataFrame(
        rows, columns=["Service Account Name", "Vault", "Path", "Created Date", "Notes"]
    ), len(unique_accounts)


def main():
    ap = argparse.ArgumentParser(
        description="Match service account names against vault key names."
    )
    ap.add_argument("service_accounts", help="Service account sheet (.csv/.xlsx/.xls)")
    ap.add_argument("vault", help="Vault sheet (.csv/.xlsx/.xls)")
    ap.add_argument("-o", "--output", default="vault_matches.csv",
                    help="Output file; .csv or .xlsx (default: vault_matches.csv)")
    ap.add_argument("--match", choices=["exact", "contains"], default="exact",
                    help="exact = whole key name must equal the account name (default). "
                         "contains = account name may appear inside a longer key name.")
    ap.add_argument("--include-unmatched", action="store_true",
                    help="Also list service accounts with NO vault match "
                         "(handy for discovery).")
    ap.add_argument("--sa-name-col", default=DEFAULT_SA_NAME_COL)
    ap.add_argument("--vault-col", default=DEFAULT_VAULT_COL)
    ap.add_argument("--path-col", default=DEFAULT_PATH_COL)
    ap.add_argument("--vault-key-col", default=DEFAULT_KEY_COL)
    ap.add_argument("--date-col", default=DEFAULT_DATE_COL)
    args = ap.parse_args()

    sa_df = load_table(args.service_accounts)
    vault_df = load_table(args.vault)

    cols = {
        "sa_name": find_column(sa_df, args.sa_name_col, "service account name"),
        "vault": find_column(vault_df, args.vault_col, "vault"),
        "path": find_column(vault_df, args.path_col, "path"),
        "key": find_column(vault_df, args.vault_key_col, "key names"),
        "date": find_column(vault_df, args.date_col, "created date"),
    }

    result, n_accounts = build_matches(
        sa_df, vault_df, cols, args.match, args.include_unmatched
    )

    out = Path(args.output)
    if out.suffix.lower() == ".xlsx":
        result.to_excel(out, index=False)
    else:
        result.to_csv(out, index=False)

    matched_accounts = result.loc[result["Vault"] != "", "Service Account Name"].nunique()
    dup_rows = result["Notes"].str.contains("Duplicate record").sum()
    print(f"Service accounts checked : {n_accounts}")
    print(f"Accounts found in vault  : {matched_accounts}")
    print(f"Accounts NOT found       : {n_accounts - matched_accounts}")
    print(f"Output rows written      : {len(result)}")
    print(f"Rows flagged duplicate   : {dup_rows}")
    print(f"Saved to                 : {out}")


if __name__ == "__main__":
    main()
