# Budgets and policies
Keep spend within limits and catch costly changes before and as they happen.

## Budgets
- **Governance → Budgets**: set an amount per month, quarter or year for any slice of spend (account, service, region, team or tag), with alert thresholds and a forecast warning.
- Budgets can be nested: a team budget inside a department budget.

## Policies
- **Governance → Policies**: required tags, allowed regions, denied instance types, Kubernetes requests and the largest monthly increase allowed in one change.
- Policies are checked every morning against your resources, on every change Live Radar sees, and on Terraform plans in CI. **Critical** policies fail the CI check.

## Policies as code
- Download your policies as YAML from the Policies page and keep them in your repository. Send changes back with:

```
curl -X PUT https://YOUR-ADDRESS/api/v1/governance/policies.yaml \
  -H "Authorization: Bearer $CCRADAR_API_KEY" --data-binary @policies.yaml
```

- Add `?prune=true` to delete policies that are no longer in the file.

## Cost comments on pull requests
- The CloudCostRadar GitHub Action posts what a Terraform plan adds to the monthly bill, priced at your own rates, and the policies it breaks. See `integrations/github-action` for the workflow.
