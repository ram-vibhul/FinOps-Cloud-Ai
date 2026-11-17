# Kubernetes
See what each namespace, team and workload costs, and right-size them safely.

## Install the agent
- **Settings → Kubernetes** adds a cluster and shows the Helm command for the read-only agent. It only needs outbound HTTPS to CloudCostRadar.

```
helm upgrade --install cloudcostradar-agent CHART --set ccradar.token=YOUR-CLUSTER-TOKEN
```

## Costs and right-sizing
- Costs are split by namespace, workload and label from the node costs in your bill.
- Right-sizing compares requests with real usage and suggests new requests, with the saving.
- Incidents (crash loops, pending pods) are detected and explained, and can be investigated with the likely cause.
