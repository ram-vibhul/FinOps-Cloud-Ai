# Connect your clouds
Bring in your AWS, Azure and Google Cloud bills with read-only access.

## AWS
- In **Settings → Cloud accounts**, choose **Add AWS account**. CloudCostRadar shows a CloudFormation template that creates a read-only role. It trusts only CloudCostRadar and needs an external ID unique to your connection.
- Run the template in your management (payer) account to see every linked account's costs, or in a single account to see just that one.
- For resource-level detail, turn on a Cost and Usage Report (Data Exports) to S3 and give its bucket in the connection. Without it, CloudCostRadar uses Cost Explorer data by service and account.
- The first costs arrive within minutes; savings findings follow after the first inventory scan.

## Azure
- Create an app registration with **Cost Management Reader** on the billing scope and **Reader** on each subscription. **Settings → Cloud accounts** shows the exact `az` commands.
- Reader also lets Live Radar read the Activity Log and find unattached disks and unused IP addresses.

## Google Cloud
- Turn on the BigQuery billing export, then create a service account with the roles listed in **Settings → Cloud accounts** (BigQuery read, Recommender, Compute and Logs Viewer) and upload its key.
- Delete the local copy of the key after uploading it: CloudCostRadar stores it encrypted.

## Other services
- **Connect a service** (in **Settings → Cloud accounts**) connects other bills, each with a read-only key. Each one is labelled:
  - **Beta**: real costs from the vendor's billing API: Snowflake, Databricks, Datadog, MongoDB Atlas (per cluster, with services such as backup, data transfer and Search), OpenAI, Anthropic, GitHub, Confluent Cloud, Vercel, DigitalOcean, Linode (Akamai Cloud), Fastly, Elastic Cloud, Grafana Cloud, Oracle Cloud, Alibaba Cloud and Twilio.
  - **Alpha**: estimates, because the vendor has no billing API (or its API is still in preview). These are New Relic, Sentry and Hetzner Cloud (usage priced from rates or price lists), Okta, Slack, Zoom, PagerDuty and Atlassian Jira (seats times the price you enter, for the current month only), and Cloudflare (whose billing API is itself in preview). Check alpha figures against your invoice.
  - **Stable**: checked against live accounts. None of the connectors has been checked this way yet.
- Fivetran and dbt Cloud are not connected yet: neither has a public API for cost or usage. Upload their usage export as a file instead.
- You can also upload a CSV or FOCUS file from any provider.
