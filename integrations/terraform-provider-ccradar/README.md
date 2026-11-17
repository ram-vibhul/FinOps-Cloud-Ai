# CloudCostRadar Terraform provider

Manage CloudCostRadar **budgets**, **policies** and **virtual tags** as code
(tracker 11.23). The provider calls the public REST API with an organisation API key,
so it changes only CloudCostRadar settings, never your cloud accounts.

| Resource | What it is |
|---|---|
| `ccradar_budget` | A budget with thresholds, forecast alerts and email recipients; can sit under another budget. |
| `ccradar_policy` | A governance policy (required tags, allowed regions, denied instance types, Kubernetes requests, monthly increase). |
| `ccradar_virtual_tag` | A virtual tag worked out from rules over the bill. |

All three can be imported by ID (`terraform import ccradar_budget.web <id>`). A change
made in the app shows as a difference at the next plan.

## Configure

```hcl
terraform {
  required_providers {
    ccradar = { source = "cloudcostradar/ccradar" }
  }
}

provider "ccradar" {
  endpoint = "https://ccradar.example.com" # or CCRADAR_ENDPOINT
  # api_key: set CCRADAR_API_KEY to an organisation API key with the admin scope
}
```

Create the key under **Settings → API keys** with the *admin* scope (policies and
virtual tags need it). Keep it in your CI's secret store, never in the `.tf` files.

See [examples/main.tf](examples/main.tf) for one of each resource.

## Build and test

```bash
go test ./...
go build -o terraform-provider-ccradar
```

To try a local build, point Terraform at it with a `dev_overrides` block in
`~/.terraformrc`. Publishing to the Terraform Registry needs a public GitHub
repository named `terraform-provider-ccradar`, a GPG signing key and a release
workflow. That is an owner decision and has not been done.

## Licences

Built on HashiCorp's terraform-plugin-framework and terraform-plugin-go (MPL-2.0),
which allow use in closed-source software; their source files are not modified here.
