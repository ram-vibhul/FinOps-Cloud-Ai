# CloudCostRadar cost estimate (GitHub Action)

Comments on each pull request with what a Terraform plan or a Kubernetes change adds to
the monthly bill. Instances are priced at **your own rates** from your bill in
CloudCostRadar, falling back to list prices, and nothing unknown is guessed. The Action
only reads: it sends the plan to your CloudCostRadar and posts one comment, updated on
every push.

## Set up

1. In CloudCostRadar, Settings → API keys: create a key (read access is enough).
2. In the repository, add it as the secret `CCRADAR_API_KEY`.
3. Add a workflow:

```yaml
name: Cost estimate
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  cost:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: hashicorp/setup-terraform@v3
      - run: terraform init -input=false && terraform plan -out=plan.out -input=false
      - run: terraform show -json plan.out > plan.json
      - uses: ./.github/actions/cloudcostradar   # a copy of integrations/github-action (not yet published)
        with:
          base-url: https://app.cloudcostradar.com
          api-key: ${{ secrets.CCRADAR_API_KEY }}
          plan: plan.json
          fail-above: "500"        # optional: fail the check above +500 a month
```

For Kubernetes, set `kind: kubernetes`, `plan:` to the changed manifests and `before:`
to the current ones (for example from `git show origin/main:k8s/app.yaml`).

## Inputs and outputs

| Input | Default | |
|---|---|---|
| `base-url` | | Your CloudCostRadar address (https) |
| `api-key` | | Organisation API key, from a secret |
| `kind` | `terraform` | `terraform` or `kubernetes` |
| `plan` | | Plan JSON, or manifests |
| `before` | | Kubernetes: current manifests |
| `fail-above` | | Fail when the monthly increase is above this |
| `comment` | `true` | Post or update the pull-request comment |

Outputs: `change` (monthly change) and `currency`. The table also goes to the job
summary.

The plan is sent to your CloudCostRadar only; it may contain resource names and
variable values, so use your own installation's address. Plans of up to 20 MB are
accepted.
