# Vault credential tracking with custom metadata

## The problem

Our Vault holds everything: database names, host/ports, certificates, service account passwords, tokens. Only a subset *expires or needs rotating*, and nothing in Vault tells us which. Reporting means someone manually hunting through paths.

## The approach

Tag each secret with **KV v2 custom metadata**. It is stored next to the secret, not inside it, so it can be read and reported on **without ever reading a secret value**.

Then four steps, each a command in `vault_tagger.py`:

```
 plan  ──►  review  ──►  apply  ──►  report
(dry run)   (edit JSON)  (writes)   (read-only, scheduled)
                                       ▲
              rotated ─────────────────┘  (run after each rotation to keep tags fresh)
```

| Command | Writes to Vault? | What it does |
|---|---|---|
| `plan` | **No** | Walks Vault, applies `rules.json`, writes `plan.json` (and optional CSV) of recommended tags |
| `apply` | Yes (`--dry-run` available) | Applies the approved entries from `plan.json` |
| `report` | No | Inventory + status: overdue / due-soon / ok / no-schedule / untagged |
| `rotated` | Yes (`--dry-run` available) | Stamps `last_rotated` and `next_rotation` on one secret after a rotation |

## Tag schema

All values are strings. Dates are ISO `YYYY-MM-DD`.

| Key | Example | Meaning |
|---|---|---|
| `type` | `service-account` | `service-account`, `certificate`, `token`, `password`, or `config` (tagged, nothing to renew) |
| `owner` | `payments` | Who to chase when it is due |
| `system` | `oracle-prod-01` | Where the account/cert actually lives (optional, mostly set by hand) |
| `rotation_days` | `90` | Policy interval |
| `rotation_method` | `manual` | `manual`, `automated`, or `vault-managed` |
| `last_rotated` | `2026-09-15` | Last renewal |
| `next_rotation` | `2026-12-14` | Computed: `last_rotated + rotation_days` |
| `expiry_date` | `2027-03-01` | Hard expiry, mainly for certs and tokens. The report uses the earlier of this and `next_rotation` |
| `review_status` | `auto-inferred` | Set to `confirmed` once a human has checked the tags |
| `tagged_by` / `tagged_on` | `vault_tagger` / `2026-10-08` | Audit stamps |

`type=config` is how database names, ports, and hostnames get out of the way: they are tagged as not tracked instead of showing up as noise.

## Workflow

### 1. Dry run: get recommendations

```bash
export VAULT_ADDR=https://vault.example.com
export VAULT_TOKEN=...

python vault_tagger.py plan --rules rules.json --out plan.json --csv plan.csv
```

Nothing is written to Vault. The output is a summary plus `plan.json`. Each entry has:

- `set`: tags that would be **added** (only tags that are currently missing)
- `conflicts`: places where an existing tag differs from the recommendation (**never applied** unless you use `--overwrite`)
- `rule` and `confidence`: why it was recommended
- `approve`: `true` for confidence at or above the threshold in `rules.json` (default `medium`), `false` for `low`

`plan.json` also lists `unclassified` secrets, meaning those that matched no rule. Use that list to improve `rules.json`, then re-run `plan` as often as you like.

You can scope a run with `--mount`, `--prefix`, or `--paths-file` (a text file of `mount/path` lines, handy if your existing report already produces a list of paths).

### 2. Review

Open `plan.json` or `plan.csv`. Set `"approve": false` on anything you do not want, or flip a `low` entry to `true` if the guess is right. Edit `set` values directly if a recommendation is slightly off (for example a different `rotation_days`).

### 3. Apply

```bash
python vault_tagger.py apply --plan plan.json --dry-run     # preview exactly what will be written
python vault_tagger.py apply --plan plan.json               # asks you to type 'apply'
python vault_tagger.py apply --plan plan.json --only '^secret/service-accounts/'   # a slice at a time
```

Safeguards:

- Only approved entries are applied.
- Existing tags are kept and new ones are merged in.
- If a secret's metadata changed since the plan was made, that entry is skipped and reported.
- Other metadata settings (`max_versions`, `cas_required`, `delete_version_after`) are preserved, and each write is read back and verified.
- An `apply_log_*.json` is written with the **before** values, so any change can be reversed.

### 4. Report

```bash
python vault_tagger.py report --warn-days 30 --csv report.csv
python vault_tagger.py report --status overdue
python vault_tagger.py report --owner payments --type certificate
python vault_tagger.py report --fail-on-overdue     # exit code 2 if anything is overdue
```

| Status | Meaning |
|---|---|
| `overdue` | Due date has passed |
| `due-soon` | Due within `--warn-days` |
| `ok` | Due later |
| `no-schedule` | Tagged as a credential but no `next_rotation` or `expiry_date` |
| `bad-date` | A date tag is not `YYYY-MM-DD` |
| `untagged` | No `type` tag. This is the "what haven't we classified yet" list (`--all` to show) |
| `not-tracked` | `type=config` |

### 5. Keep it current

Tags only stay useful if rotation updates them. At the end of any rotation job (CI, Ansible, a runbook step), run:

```bash
python vault_tagger.py rotated --mount secret --path service-accounts/payments/oracle-prod
# certs / tokens with a known hard expiry:
python vault_tagger.py rotated --mount secret --path apps/web/tls-cert --expiry-date 2027-08-01
```

## The rules file

`rules.json` is an ordered list. **First match wins**, so specific rules go above general ones.

```json
{
  "name": "service-account-by-folder",
  "confidence": "high",
  "match": { "path_regex": "^[^/]+/service[-_]?accounts?/(?P<owner>[^/]+)/" },
  "set": { "type": "service-account", "owner": "{owner}", "rotation_days": "90", "rotation_method": "manual" }
}
```

- `path_regex` is matched (case-insensitive) against `<mount>/<path>`. It can be a string or a list. Remember the mount name is part of the string, so avoid keywords that could match your mount name.
- `exclude_regex` (optional) vetoes a match.
- Named groups become variables: `(?P<owner>...)` is usable as `{owner}`. `{mount}`, `{path}`, and `{basename}` are always available.
- `"action": "skip"` leaves matching secrets alone.
- `confidence` is `high`, `medium`, or `low`. Entries below `auto_approve_min_confidence` come out with `approve: false`.
- Dates are inferred automatically: if `rotation_days` is known, `last_rotated` defaults to the secret's last-written time in Vault and `next_rotation` is computed from it.

**The starter rules are guesses based on common naming.** Expect to tune the regexes to your real path conventions. The first dry run is for exactly that.

## Permissions

No policy needs access to secret *data*, which is deliberate.

Reporting (read-only):

```hcl
path "sys/mounts"        { capabilities = ["read"] }
path "secret/metadata/*" { capabilities = ["list", "read"] }
```

Tagging (add `update` on metadata; do **not** grant `delete` on metadata, which removes all versions):

```hcl
path "sys/mounts"        { capabilities = ["read"] }
path "secret/metadata/*" { capabilities = ["list", "read", "update"] }
```

Repeat the `metadata/*` path for each KV v2 mount, or pass `--mount`. A good split is a read-only identity for the scheduled report and a separate identity for `apply` and `rotated`.

## Running it on a schedule

A weekly job that fails loudly on overdue items:

```bash
python vault_tagger.py report --csv /reports/vault-$(date +%F).csv --fail-on-overdue \
  || notify-team "Vault has overdue credentials, see the attached report"
```

Wire the failure to Slack or email in cron, Jenkins, GitHub Actions, or whatever you use.

## Integrating with your existing script

- **Auth:** replace `get_client()` with your existing login code.
- **Existing report:** if it can emit a list of paths, feed it to `plan` / `report` with `--paths-file`.

## Limitations to know about

- **KV v2 only.** KV v1 has no custom metadata.
- **Limits:** 64 pairs per secret, keys up to 128 bytes, values up to 512 bytes. The script validates this before writing.
- **Metadata is not secret.** Anyone who can read the metadata path can see it. Never put sensitive values in tags.
- **Tags are labels, not enforcement.** Nothing alerts by itself; the scheduled `report` does that.
- **Inferred dates are approximations.** `last_rotated` defaults to when the secret was last written. For `vault-managed` credentials (dynamic or static roles rotated by Vault), that timestamp is not meaningful, so set those tags by hand and consider excluding them via a rule.
- **Certificate expiry is not read from the certificate.** That would require reading the secret value. Use `expiry_date` (via `rotated --expiry-date`) for hard expiries. If you want automatic extraction later, it would need a deliberately scoped, separate job.
- **Test first.** The script was tested against an in-memory fake of the `hvac` API, not a live Vault. Pilot it on a non-production mount with `plan`, `apply --dry-run`, and a small `--only` slice before running it broadly.

## Suggested rollout

1. Run `plan` on one non-prod mount. Tune `rules.json` until `unclassified` is small and the recommendations look right.
2. `apply` a small slice (`--only`) and run `report`. Confirm the numbers match reality.
3. Roll out mount by mount. Have owners flip `review_status` to `confirmed` as they verify their tags.
4. Add `rotated` to the rotation jobs and schedule the weekly `report`.
5. Look at what is left in `no-schedule` and `untagged`. That is your real remaining inventory gap.
6. Where Vault can rotate things itself (database secrets engine, cloud engines), move them to `vault-managed` and take them off the manual list.
