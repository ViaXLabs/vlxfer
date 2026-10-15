#!/usr/bin/env bash
# =============================================================================
# rancher_eks_export.sh
#
# Pulls EKS cluster data out of Rancher and writes report-ready CSVs.
#
# Outputs (in $OUT_DIR):
#   clusters.csv     one row per EKS cluster Rancher knows about
#   nodegroups.csv   one row per EKS node group (Rancher-provisioned clusters)
#   nodes.csv        one row per Kubernetes node (live, via Rancher's k8s proxy)
#   deployments.csv  one row per Deployment (only if INCLUDE_WORKLOADS=true)
#   raw/*.json       untouched API responses, handy for debugging / new columns
#
# Required env:
#   RANCHER_URL      e.g. https://rancher.example.com   (no trailing /v3)
#   RANCHER_TOKEN    Rancher API bearer token (token-xxxxx:yyyyyyyy)
#
# Optional env:
#   OUT_DIR             default ./rancher_export
#   INCLUDE_WORKLOADS   true|false, default false
#   INCLUDE_LOCAL       true|false, include Rancher's own "local" cluster, default false
#   STRICT              true|false, exit non-zero if any cluster fails, default false
#   CURL_EXTRA          extra curl flags, e.g. "--cacert /etc/ssl/rancher-ca.pem"
#
# Requires: bash 4+, curl, jq 1.6+
# =============================================================================
set -euo pipefail

: "${RANCHER_URL:?RANCHER_URL is required}"
: "${RANCHER_TOKEN:?RANCHER_TOKEN is required}"
RANCHER_URL="${RANCHER_URL%/}"
OUT_DIR="${OUT_DIR:-./rancher_export}"
INCLUDE_WORKLOADS="${INCLUDE_WORKLOADS:-false}"
INCLUDE_LOCAL="${INCLUDE_LOCAL:-false}"
STRICT="${STRICT:-false}"
CURL_EXTRA="${CURL_EXTRA:-}"

for tool in curl jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "ERROR: $tool is not installed" >&2; exit 1; }
done

mkdir -p "$OUT_DIR/raw"
EXPORT_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
FAILED_CLUSTERS=()

log()  { echo "[$(date -u +%H:%M:%S)] $*"; }
warn() { echo "[$(date -u +%H:%M:%S)] WARN: $*" >&2; }

# --- HTTP helper ------------------------------------------------------------
# One GET against Rancher. Fails (non-zero) on HTTP errors so callers can react.
api() {
  # shellcheck disable=SC2086
  curl -sS --fail --retry 3 --retry-delay 2 --max-time 60 $CURL_EXTRA \
    -H "Authorization: Bearer ${RANCHER_TOKEN}" \
    -H "Accept: application/json" \
    "$1"
}

# --- Rancher v3 API list, following pagination.next -------------------------
# Prints a JSON array of every item in .data across all pages.
v3_list() {
  local url="$1" tmp i=0
  tmp="$(mktemp -d)"
  while [[ -n "$url" ]]; do
    api "$url" > "$tmp/$i.json" || { rm -rf "$tmp"; return 1; }
    url="$(jq -r '.pagination.next // empty' "$tmp/$i.json")"
    i=$((i + 1))
  done
  jq -s '[.[].data[]]' "$tmp"/*.json
  rm -rf "$tmp"
}

# --- Kubernetes API list through Rancher's proxy, following continue tokens --
# $1 = Rancher cluster ID (c-xxxxx), $2 = k8s API path (/api/v1/nodes)
# Prints a JSON array of every item across all pages.
k8s_list() {
  local cluster="$1" path="$2" tmp i=0 cont="" url
  tmp="$(mktemp -d)"
  while :; do
    url="$RANCHER_URL/k8s/clusters/$cluster$path?limit=500"
    if [[ -n "$cont" ]]; then
      url="$url&continue=$(jq -rn --arg c "$cont" '$c|@uri')"
    fi
    api "$url" > "$tmp/$i.json" || { rm -rf "$tmp"; return 1; }
    cont="$(jq -r '.metadata.continue // empty' "$tmp/$i.json")"
    i=$((i + 1))
    [[ -z "$cont" ]] && break
  done
  jq -s '[.[].items[]]' "$tmp"/*.json
  rm -rf "$tmp"
}

# =============================================================================
# 1. Clusters
# =============================================================================
log "Fetching clusters from $RANCHER_URL"
v3_list "$RANCHER_URL/v3/clusters?limit=100" > "$OUT_DIR/raw/clusters.json"

# Keep only EKS clusters. Rancher marks them in different ways depending on
# whether Rancher provisioned them or they were imported, so check all three.
jq --arg incl "$INCLUDE_LOCAL" '
  [ .[]
    | select($incl == "true" or .id != "local")
    | select( (.provider // "" | ascii_downcase) == "eks"
           or (.driver   // "" | ascii_downcase) == "eks"
           or .eksConfig != null )
  ]' "$OUT_DIR/raw/clusters.json" > "$OUT_DIR/raw/eks_clusters.json"

CLUSTER_COUNT="$(jq 'length' "$OUT_DIR/raw/eks_clusters.json")"
log "Found $CLUSTER_COUNT EKS cluster(s)"

jq -r --arg ts "$EXPORT_TS" '
  ["export_ts","cluster_id","cluster_name","display_name","state","provider","driver",
   "provisioned_by_rancher","aws_region","k8s_version","node_count","created","labels"],
  ( .[] | [
      $ts,
      .id,
      .name,
      (.eksConfig.displayName // .name),
      (.state // ""),
      (.provider // ""),
      (.driver // ""),
      (if .eksConfig != null and ((.eksConfig.imported // false) | not)
         then "true" else "false" end),
      (.eksConfig.region // ""),
      (.version.gitVersion // .eksConfig.kubernetesVersion // ""),
      (.nodeCount // 0),
      (.created // ""),
      ((.labels // {}) | to_entries | map("\(.key)=\(.value)") | join(";"))
    ])
  | @csv' "$OUT_DIR/raw/eks_clusters.json" > "$OUT_DIR/clusters.csv"

# =============================================================================
# 2. Node groups (from Rancher's EKS config; empty for most imported clusters)
# =============================================================================
jq -r --arg ts "$EXPORT_TS" '
  ["export_ts","cluster_id","cluster_name","nodegroup","instance_type",
   "desired_size","min_size","max_size","disk_gb","k8s_version","spot"],
  ( .[] as $c
    | ($c.eksConfig.nodeGroups // [])[]
    | [ $ts, $c.id, $c.name,
        (.nodegroupName // ""),
        (.instanceType // ""),
        (.desiredSize // ""),
        (.minSize // ""),
        (.maxSize // ""),
        (.diskSize // ""),
        (.version // ""),
        (if .requestSpotInstances == true then "true" else "false" end) ])
  | @csv' "$OUT_DIR/raw/eks_clusters.json" > "$OUT_DIR/nodegroups.csv"

# =============================================================================
# 3. Nodes and (optionally) deployments, per cluster via Rancher's k8s proxy
# =============================================================================
NODES_CSV="$OUT_DIR/nodes.csv"
DEPLOY_CSV="$OUT_DIR/deployments.csv"

echo '"export_ts","cluster_id","cluster_name","node","nodegroup","instance_type","zone","capacity_type","kubelet_version","os_image","cpu","memory_gib","max_pods","ready","created"' > "$NODES_CSV"
if [[ "$INCLUDE_WORKLOADS" == "true" ]]; then
  echo '"export_ts","cluster_id","cluster_name","namespace","deployment","replicas_desired","replicas_ready","images","created"' > "$DEPLOY_CSV"
fi

NODE_COUNT=0
while IFS=$'\t' read -r cid cname cstate; do
  if [[ "$cstate" != "active" ]]; then
    warn "Skipping $cname ($cid): state is '$cstate'"
    FAILED_CLUSTERS+=("$cname")
    continue
  fi

  log "Nodes: $cname ($cid)"
  if ! k8s_list "$cid" "/api/v1/nodes" > "$OUT_DIR/raw/nodes_${cid}.json"; then
    warn "Could not read nodes for $cname ($cid), check token permissions on this cluster"
    FAILED_CLUSTERS+=("$cname")
    continue
  fi

  jq -r --arg ts "$EXPORT_TS" --arg cid "$cid" --arg cname "$cname" '
    def gib: if . == null then ""
             elif endswith("Ki") then (rtrimstr("Ki") | tonumber / 1048576 * 100 | round / 100)
             elif endswith("Mi") then (rtrimstr("Mi") | tonumber / 1024 * 100 | round / 100)
             elif endswith("Gi") then (rtrimstr("Gi") | tonumber)
             else . end;
    .[] | (.metadata.labels // {}) as $l | [
      $ts, $cid, $cname,
      .metadata.name,
      ($l["eks.amazonaws.com/nodegroup"] // $l["karpenter.sh/nodepool"] // ""),
      ($l["node.kubernetes.io/instance-type"] // $l["beta.kubernetes.io/instance-type"] // ""),
      ($l["topology.kubernetes.io/zone"] // $l["failure-domain.beta.kubernetes.io/zone"] // ""),
      ($l["eks.amazonaws.com/capacityType"] // $l["karpenter.sh/capacity-type"] // ""),
      (.status.nodeInfo.kubeletVersion // ""),
      (.status.nodeInfo.osImage // ""),
      (.status.capacity.cpu // ""),
      (.status.capacity.memory | gib),
      (.status.capacity.pods // ""),
      ([.status.conditions[]? | select(.type == "Ready") | .status][0] // "Unknown"),
      (.metadata.creationTimestamp // "")
    ] | @csv' "$OUT_DIR/raw/nodes_${cid}.json" >> "$NODES_CSV"

  n="$(jq 'length' "$OUT_DIR/raw/nodes_${cid}.json")"
  NODE_COUNT=$((NODE_COUNT + n))

  if [[ "$INCLUDE_WORKLOADS" == "true" ]]; then
    log "Deployments: $cname ($cid)"
    if k8s_list "$cid" "/apis/apps/v1/deployments" > "$OUT_DIR/raw/deployments_${cid}.json"; then
      jq -r --arg ts "$EXPORT_TS" --arg cid "$cid" --arg cname "$cname" '
        .[] | [
          $ts, $cid, $cname,
          .metadata.namespace,
          .metadata.name,
          (.spec.replicas // 0),
          (.status.readyReplicas // 0),
          ([.spec.template.spec.containers[]?.image] | join(";")),
          (.metadata.creationTimestamp // "")
        ] | @csv' "$OUT_DIR/raw/deployments_${cid}.json" >> "$DEPLOY_CSV"
    else
      warn "Could not read deployments for $cname ($cid)"
      FAILED_CLUSTERS+=("$cname")
    fi
  fi
done < <(jq -r '.[] | [.id, .name, (.state // "unknown")] | @tsv' "$OUT_DIR/raw/eks_clusters.json")

# =============================================================================
# 4. Summary + values for Harness output variables
# =============================================================================
FAILED_COUNT="${#FAILED_CLUSTERS[@]}"
FAILED_LIST="$(IFS=,; echo "${FAILED_CLUSTERS[*]:-}")"
OUTPUT_DIR="$(cd "$OUT_DIR" && pwd)"
export EXPORT_TS CLUSTER_COUNT NODE_COUNT FAILED_COUNT FAILED_LIST OUTPUT_DIR

log "Done. clusters=$CLUSTER_COUNT nodes=$NODE_COUNT failed=$FAILED_COUNT ${FAILED_LIST:+($FAILED_LIST)}"
log "Files in $OUTPUT_DIR:"
ls -1 "$OUTPUT_DIR"/*.csv

if [[ "$STRICT" == "true" && "$FAILED_COUNT" -gt 0 ]]; then
  echo "ERROR: $FAILED_COUNT cluster(s) failed and STRICT=true" >&2
  exit 1
fi
