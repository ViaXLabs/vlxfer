Let me test it against a mock Rancher API to make sure the parsing and pagination work.

That script handles getting the data out of Rancher. Your existing template can then take the CSVs and handle merging and Confluence publishing.

## Where the data comes from

Rancher gives you two ways to get data, and the script uses both.

**The Rancher v3 API** (`/v3/clusters`) is Rancher's own inventory of clusters. It returns cluster ID, name, state, Kubernetes version, node count, and labels. For EKS clusters that Rancher provisioned, it also returns the EKS config, including AWS region and node group definitions (instance type, min/max/desired size). This is fast because it's a single call, and it describes clusters the way Rancher sees them.

**Rancher's Kubernetes proxy** (`/k8s/clusters/<cluster-id>/...`) passes regular Kubernetes API calls through to each cluster, using the same token. You get live data from the cluster itself: actual nodes, their instance types, availability zones, spot or on-demand, kubelet versions, readiness, and deployments if you need them. You don't need kubeconfigs or AWS credentials. If a request works with `kubectl get`, it works through the proxy.

## What the script outputs

The script writes four CSVs to `OUT_DIR`:

| File | Contents |
|---|---|
| `clusters.csv` | One row per EKS cluster |
| `nodegroups.csv` | Node groups from Rancher's EKS config |
| `nodes.csv` | Live nodes from each cluster |
| `deployments.csv` | Deployments, only if `INCLUDE_WORKLOADS=true` |

Every row includes `export_ts`, `cluster_id`, and `cluster_name`, so the files join to each other cleanly. `export_ts` also lets you track history if you keep appending exports over time.

The untouched API responses are saved in `raw/`. When you want a new column, open the raw JSON, find the field, and add one line to the matching `jq` block.

A few behaviors to know about:

- **EKS detection:** Rancher marks EKS clusters in different ways depending on whether it provisioned them or they were imported, so the script checks `provider`, `driver`, and `eksConfig`.
- **Rancher's own cluster:** the `local` cluster is skipped unless you set `INCLUDE_LOCAL=true`.
- **Unreachable clusters:** clusters that aren't `active`, or that the token can't read, are logged as warnings and the script continues. Set `STRICT=true` if you want the pipeline to fail instead.
- **Pagination:** both APIs page their results, and the script follows the pages, so large estates won't get cut off.

## Setting up the token

1. In Rancher, go to your avatar, then **Account & API Keys**, and create a key.
2. **Leave the scope empty.** A key scoped to one cluster can't list the others.
3. Set an expiry and plan to rotate it.
4. Use a dedicated service user if you can. For reporting it only needs read access on each cluster. The Cluster Owner role that Harness's Rancher connector asks for is for deployments, which this script doesn't do.
5. Store the key as a Harness secret, for example `rancher_readonly_token`.

**Watch out for missing clusters.** Rancher only returns clusters the user is allowed to see. If a cluster is missing from your report, the cause is almost always the token's permissions, not the script.

## Running it in Harness

You can use either kind of step.

**Shell Script step (runs on your delegate).** The delegate needs network access to Rancher, plus `curl` and `jq` installed. You can add those through the delegate's INIT_SCRIPT or a custom delegate image. Paste the script inline, or keep it in Harness File Store or git, then set the variables:

```bash
export RANCHER_URL="<+pipeline.variables.rancher_url>"
export RANCHER_TOKEN="<+secrets.getValue("rancher_readonly_token")>"
export OUT_DIR="/tmp/rancher_export"
# ...script body...
```

**Run step (CI/Build stage).** Use any image that has bash, curl, and jq, and pass the values as environment variables:

```yaml
envVariables:
  RANCHER_URL: <+pipeline.variables.rancher_url>
  RANCHER_TOKEN: <+secrets.getValue("rancher_readonly_token")>
  OUT_DIR: /harness/rancher_export
command: bash scripts/rancher_eks_export.sh
outputVariables:
  - name: CLUSTER_COUNT
  - name: NODE_COUNT
  - name: FAILED_COUNT
```

At the end, the script exports `CLUSTER_COUNT`, `NODE_COUNT`, `FAILED_COUNT`, `FAILED_LIST`, `OUTPUT_DIR`, and `EXPORT_TS`. Declare any of these as output variables if you want them in the Confluence page header, for example "Generated at X, 14 clusters, 2 unreachable."

Point `OUT_DIR` at wherever your template picks up files. Harness masks the secret in logs automatically.

## Joining with other data

`cluster_id` (for example `c-abc12`) is Rancher's stable ID, but other systems won't know it. For joining against AWS data such as cost reports or a CMDB, cluster name plus `aws_region` is usually the better key.

For imported clusters, Rancher doesn't store the region or AWS account. Those columns will be empty, and the nodes' zone labels are the fallback. Cluster labels are flattened into one `key=value;key=value` column. If your teams label clusters with things like `env` or `team`, you can split that column in your template to group the report.

## Other gotchas

- **Self-signed Rancher certificate:** set `CURL_EXTRA="--cacert /path/to/ca.pem"` rather than using `-k`.
- **Very large clusters:** leave deployments off unless you need them, because they add a lot of API calls.
- **Commas and quotes in names:** the CSVs use proper quoting, so make sure your template reads them with a real CSV parser, not a split on commas.

I checked the script's syntax and tested its HTTP and pagination flow against a mock Rancher server. My sandbox has no `jq`, though, so I couldn't run the `jq` transforms end to end. Run it once by hand against your Rancher with a small `OUT_DIR` before wiring it into the pipeline. If a field comes out empty, check the matching file in `raw/` to see what your Rancher version actually calls it.