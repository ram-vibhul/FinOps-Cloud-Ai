# Savings and automatic fixes
Find waste, plan what to do first, and let CloudCostRadar make the safe changes for you.

## Findings
- **Savings** lists opportunities across AWS, Azure, Google Cloud and Kubernetes: idle and unattached resources, right-sizing, Graviton, old generations, commitments and more. Each finding shows the saving from your own bill, the risk, the effort and the commands to do it.
- A resource counts once in the totals even when several findings mention it.
- **Plan** sorts findings into this week, this month and later.

## What if…
- Tick the changes you are considering, and optionally a Savings Plan, to see the monthly bill after them. The Savings Plan is sized on the compute left after the other changes.

## Automatic fixes
- Off by default. An owner turns them on per AWS account with a separate role and limits (actions a day, spend a month).
- Each fix needs a second person to approve it, and every safety check is run again just before acting: resources tagged `ccradar-protect` are never touched, volumes get a snapshot first.

## Office hours
- Stop test and staging servers at night and at weekends (about 64% less for those servers). A member asks for a schedule, an administrator approves it.
- Only servers CloudCostRadar stopped are started again in the morning.
