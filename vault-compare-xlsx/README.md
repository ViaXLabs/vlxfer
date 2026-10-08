# vault_match.py

Matches service account names against the key names in a vault export, so you can see where each service account lives in the vault and when its secret was created.

## What it does

1. Reads your **service account sheet** and your **vault sheet** (CSV or Excel).
2. For each service account name, searches the vault sheet's **Key Names** column.
3. Writes one output row for every vault hit, with these columns:

| Column | Source |
|---|---|
| Service Account Name | Service account sheet |
| Vault | Vault sheet |
| Path | Vault sheet |
| Created Date | Vault sheet (when the secret was created in the vault; a hint about expiry, not the expiry itself) |
| Notes | Added by the script (see below) |

If a service account matches several vault rows (for example, other teams use the same name), **all** of them are listed and each is flagged as a duplicate.

## Requirements

- Python 3.8 or newer
- `pandas`
- `openpyxl` (only for `.xlsx` files)
- `xlrd` (only for old `.xls` files)

```
pip install pandas openpyxl
```

## Usage

```
python vault_match.py <service_accounts_file> <vault_file> [options]
```

Example:

```
python vault_match.py service_accounts.xlsx vault_export.csv -o matches.csv
```

Both inputs can be `.csv`, `.xlsx` or `.xls`, and you can mix formats.

### Options

| Option | Description | Default |
|---|---|---|
| `-o`, `--output` | Output file. Use `.csv` or `.xlsx`. | `vault_matches.csv` |
| `--match exact` | The key name must equal the service account name. | on |
| `--match contains` | The service account name may appear inside a longer key name (for example `svc-billing` also finds `svc-billing-legacy`). | off |
| `--include-unmatched` | Also list service accounts that were not found in the vault. | off |
| `--sa-name-col` | Header of the service account name column. | `Service Account Name` |
| `--vault-col` | Header of the vault column. | `Vault` |
| `--path-col` | Header of the path column. | `Path` |
| `--vault-key-col` | Header of the key names column. | `Key Names` |
| `--date-col` | Header of the created date column. | `Created Date` |

Column headers are matched ignoring upper/lower case and extra spaces. If a header can't be found, the script stops and prints the columns it did find, so you can pass the right name with the matching option. Example:

```
python vault_match.py sa.csv vault.csv --sa-name-col "Account Name" --vault-key-col "Secret Name"
```

## Matching rules

- Comparison ignores case and leading/trailing/extra spaces, so `User`, `user` and `user ` all match.
- Each distinct service account is searched once, even if it is listed several times in the service account sheet.
- Everything is read as text, so dates and IDs are never reformatted.

## The Notes column

| Note | Meaning |
|---|---|
| `Duplicate record (N vault matches)` | The service account was found in N vault rows. Every one of those rows is in the output. |
| `Partial match on key name: <name>` | Only with `--match contains`. The key name is longer than the account name. |
| `name listed Nx in service account sheet` | The same account appears N times in the service account sheet. |
| `No match in vault` | Only with `--include-unmatched`. The account was not found. |

Several notes on one row are separated by semicolons.

## Example

Service account sheet:

| Service Account Name | Owner |
|---|---|
| user | TeamA |
| svc-billing | TeamB |
| svc-orphan | TeamC |

Vault sheet:

| Vault | Path | Key Names | Created Date |
|---|---|---|---|
| prod-vault | /apps/a | user | 2023-01-05 |
| prod-vault | /apps/b | User | 2022-07-19 |
| dev-vault | /apps/d | svc-billing | 2021-09-30 |

Output (with `--include-unmatched`):

| Service Account Name | Vault | Path | Created Date | Notes |
|---|---|---|---|---|
| user | prod-vault | /apps/a | 2023-01-05 | Duplicate record (2 vault matches) |
| user | prod-vault | /apps/b | 2022-07-19 | Duplicate record (2 vault matches) |
| svc-billing | dev-vault | /apps/d | 2021-09-30 | |
| svc-orphan | | | | No match in vault |

## Summary printed when it finishes

```
Service accounts checked : 3
Accounts found in vault  : 2
Accounts NOT found       : 1
Output rows written      : 4
Rows flagged duplicate   : 2
Saved to                 : vault_matches.csv
```

## Troubleshooting

- **"could not find the ... column"**: the header in your file differs from the default. Use the matching `--*-col` option.
- **No matches when you expect some**: the vault key names may be longer than the account names. Try `--match contains`.
- **Too many matches with `--match contains`**: short account names (like `user`) match inside many longer names. Use exact matching, or review the "Partial match" notes.
- **Excel file won't open**: install `openpyxl` (`.xlsx`) or `xlrd` (`.xls`), or save the sheet as CSV.

## Ideas for next steps

- Combine the output with other teams' records and resolve the duplicates.
- Compare the vault date and vault path columns already in the service account sheet against what the vault actually shows.
