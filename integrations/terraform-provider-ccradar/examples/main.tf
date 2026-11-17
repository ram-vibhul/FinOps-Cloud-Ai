terraform {
  required_providers {
    ccradar = { source = "cloudcostradar/ccradar" }
  }
}

provider "ccradar" {
  endpoint = "https://ccradar.example.com"
}

resource "ccradar_budget" "platform" {
  name       = "Platform"
  amount     = 20000
  currency   = "USD"
  thresholds = [80, 100]
  recipients = ["platform-leads@example.com"]
}

resource "ccradar_budget" "web" {
  name         = "Web shop"
  parent_id    = ccradar_budget.platform.id
  amount       = 6000
  filters_json = jsonencode({ "tag:team" = ["web"] })
}

resource "ccradar_policy" "tags" {
  name        = "Every resource has an owner"
  kind        = "required_tags"
  params_json = jsonencode({ tags = ["team", "env"] })
  severity    = "critical"
}

resource "ccradar_virtual_tag" "team" {
  key         = "team"
  description = "Team from the account when the tag is missing"
  rules_json = jsonencode([
    { value = "data", match = { account = ["111122223333"] } },
  ])
}
