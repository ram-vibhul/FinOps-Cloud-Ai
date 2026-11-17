# Live Radar
See what each infrastructure change costs minutes after it is made, before the bill shows it.

## What it shows
- Radar reads your AWS accounts' CloudTrail every 10 minutes, and Azure Activity Logs and Google Cloud audit logs every 15. Launches, resizes, deletions, pauses and scaling of servers, databases, disks, load balancers, gateways, Redshift and Auto Scaling groups are priced as a monthly run rate.
- Prices come from your own bill where it has them, otherwise from list prices for the region. Each row says which.
- **Not in the bill yet** adds up the changes made after the last day of billing data.

## Alerts
- Costly changes (100 a month or more, adjustable in **Settings → General**) are sent to the inbox, email, Slack or Teams, with buttons to open the change or mark it as expected.
- If a change will push a budget over this month, the budget's owners are told straight away.
- If a change breaks one of your policies (for example a denied instance type or region), that is flagged too.

## Undo and expected changes
- **How to undo** shows the AWS CLI command and console link that reverse a change. CloudCostRadar never runs it for you: check it first.
- **Mark as expected** records that a change was planned, and who said so.

## How accurate is it?
- Once the bill covers a few days after a change, Radar measures what the resource really cost and shows it next to the estimate. The accuracy of all measured changes is shown under the list.
