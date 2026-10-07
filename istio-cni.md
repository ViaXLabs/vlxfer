## Summary

- **What's happening:** istio-cni-node is running but reporting "not ready." A 503 from its readiness probe means the probe reached the container and the container said it hasn't finished installing itself on the node.
- **Most likely causes on EKS:**
  1. The VPC CNI (`aws-node`) isn't healthy on that node, so there's no `10-aws.conflist` to chain onto.
  2. istio-cni can't write to the host CNI directories (wrong paths, custom AMI, Bottlerocket).
  3. Something keeps overwriting the CNI config, or two installs are fighting.
  4. It can't reach the API server.
- **Fastest path to an answer:** read the logs from one failing pod, check `aws-node` on the same node, and compare against a healthy node.
- **Caveat:** I found no known bug specific to 1.29.2, so treat this as a general runbook.

---

## Step 1: Orient

```bash
kubectl get ds -A | grep -i istio-cni          # find the namespace; also catches duplicate installs
NS=kube-system                                  # set to whatever the line above shows
kubectl -n $NS get pods -l k8s-app=istio-cni-node -o wide
kubectl get nodes -o wide                       # AMI/OS, kernel, container runtime
```

Note whether **all** pods are unready or only some, and whether the bad ones are on newer nodes. In Rancher, the same list is under **Workloads → DaemonSets → istio-cni-node**.

## Step 2: Collect evidence

```bash
# Logs from one failing pod (the key evidence)
kubectl -n $NS logs <pod> --all-containers --tail=300
kubectl -n $NS logs <pod> --all-containers --previous      # if it has restarted

# Logs from every istio-cni pod at once, labeled by pod
kubectl -n $NS logs -l k8s-app=istio-cni-node --all-containers --tail=100 --prefix --max-log-requests=30

# Events and probe config
kubectl -n $NS describe pod <pod>
kubectl -n $NS get ds istio-cni-node -o yaml | grep -B2 -A10 readinessProbe
```

In `describe`, check **Last State**. `OOMKilled` or non-zero exit codes are important clues.

To see the 503 yourself (the image is probably distroless, so port-forward rather than exec):

```bash
kubectl -n $NS port-forward pod/<pod> 8000:8000 &
curl -i localhost:8000/readyz
```

The probe should be `/readyz` on port 8000. Verify this in the YAML.

## Step 3: Check the VPC CNI on the same node

istio-cni chains behind it, so a sick `aws-node` makes istio-cni unready.

```bash
kubectl -n kube-system get pods -l k8s-app=aws-node -o wide
kubectl -n kube-system logs <aws-node-pod-on-same-node> -c aws-node --tail=100
kubectl -n kube-system get ds aws-node -o jsonpath='{.spec.template.spec.containers[0].image}'
```

Also check that the node role has `AmazonEKS_CNI_Policy`, the subnets aren't out of IPs, and the VPC CNI version supports your EKS version.

## Step 4: Look at the node itself

Use SSM, or without SSH:

```bash
kubectl debug node/<node-name> -it --image=busybox     # host filesystem appears at /host
ls -la /host/etc/cni/net.d/ /host/opt/cni/bin/
cat /host/etc/cni/net.d/10-aws.conflist
```

Healthy looks like this:

- `10-aws.conflist` exists, with `istio-cni` in its plugins list.
- `/opt/cni/bin/istio-cni` exists.
- A `ZZZ-istio-cni-kubeconfig` file is present.

From a node shell, also grab:

```bash
journalctl -u kubelet --since "30 min ago" | tail -200
tail -100 /var/log/aws-routed-eni/plugin.log /var/log/aws-routed-eni/ipamd.log
```

## Step 5: Match the symptom to a cause

Exact log wording varies by version, so match on meaning.

| What the logs show                                            | Likely cause                                                                                                      | Fix                                                                                 |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Waiting for or can't find a CNI config file                   | `aws-node` not healthy, so `10-aws.conflist` is missing                                                           | Fix `aws-node` first (IAM, IP exhaustion, API reachability, version)                |
| Permission denied, read-only filesystem, or no such directory | Wrong host paths (custom AMI, Bottlerocket)                                                                       | Set `cniBinDir` and `cniConfDir` to the correct paths and check the hostPath mounts |
| Repeated "config changed, reinstalling" loop                  | Something keeps rewriting the conflist (aws-node restarts, add-on updates, Multus, or a second istio-cni install) | Look for duplicate installs and stop the other writer                               |
| Timeouts, x509, or connection refused to the API server       | Network, security group, NetworkPolicy, or token problem                                                          | Test API reachability from the pod or node                                          |
| Ambient-related errors                                        | Ambient mode enabled in the CNI chart but ztunnel or istiod isn't set up to match (or vice versa)                 | Make the ambient settings consistent across all components                          |
| Nothing wrong, and it goes ready after a minute               | Transient startup                                                                                                 | Watch whether it settles; if so, ignore                                             |
| Pod is `OOMKilled`                                            | Resource limits too low                                                                                           | Raise memory limits                                                                 |

Since the pods are running at all, Pod Security Admission is already ruled out as the problem.

## Step 6: Check configuration

```bash
helm list -A | grep -i istio
helm get values <release> -n $NS -a
```

If it was installed through Rancher, look under **Apps → Installed Apps** instead. Verify:

- `cniBinDir` and `cniConfDir` match your nodes.
- Set `cniConfFileName` explicitly to `10-aws.conflist` on EKS so it chains onto the right file.
- The image tag matches 1.29.2 and matches istiod. Check with `kubectl -n istio-system get deploy istiod -o jsonpath='{.spec.template.spec.containers[0].image}'`.
- Tolerations let it schedule on every node.
- The ambient setting matches your actual mode.

To turn on debug logs, find the exact key for your chart first:

```bash
helm show values istio/cni --version 1.29.2 | grep -i -B1 -A2 log
```

Set it to `debug`, upgrade, then restart.

## Step 7: Fix, from least to most disruptive

1. Delete one failing pod and see if the replacement goes ready: `kubectl -n $NS delete pod <pod>`
2. Fix `aws-node` if Step 3 showed it was unhealthy.
3. Correct the Helm values and upgrade, or `helm rollback` to the last good revision.
4. Restart the DaemonSet, which rolls node by node: `kubectl -n $NS rollout restart ds/istio-cni-node`
5. If one node is wedged, cordon it, drain it, and replace the instance.
6. Last resort, sidecar mode only: run without the CNI and use privileged `istio-init` containers instead. This is a larger change and needs Pod Security settings that allow it, so don't do it casually.

## Step 8: Verify and clean up

- The DaemonSet shows desired equal to ready: `kubectl -n $NS get ds istio-cni-node`
- `istio-cni` appears in the conflist on previously bad nodes.
- Restart application pods that started while the CNI was unhealthy, since their traffic redirection may be missing: `kubectl rollout restart deploy/<name> -n <ns>`
- Schedule a test pod in an injected namespace onto a previously bad node and confirm it comes up cleanly.
- If app pods fail with sandbox or iptables errors after the CNI is ready, check the AMI's iptables backend (nftables on AL2023) and your Istio version's notes on it.

---

## What to paste back to me

1. `kubectl get pods -o wide` for the istio-cni pods
2. Logs from one failing pod (current and `--previous`)
3. The Events and Last State sections from `describe`
4. `aws-node` status and logs from the same node
5. The `10-aws.conflist` contents from a bad node, and a good one if you have it
6. Sidecar or ambient mode, node AMI, VPC CNI version, and how it was installed (Helm, istioctl, Rancher Apps)

With those I can pin down the cause. Want me to put this runbook into a doc you can keep or share with your team?
