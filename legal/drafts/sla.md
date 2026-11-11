> **DRAFT — NOT REVIEWED BY A LAWYER. NOT FOR PUBLICATION.** Placeholders are in [SQUARE BRACKETS].

# CloudCostRadar Service Level Agreement

Last updated: [DATE]

This SLA applies to paid plans of the hosted CloudCostRadar service [ON PLANS: BUSINESS AND
ENTERPRISE — Q2]. It does not apply to free plans, trials or self-hosted CloudCostRadar.

## 1. Availability commitment

Yugiverse will make the CloudCostRadar web application and API available
[99.5 / 99.9 — Q6] % of each calendar month ("**Monthly Uptime**").

Monthly Uptime = (total minutes in the month − minutes of Downtime) ÷ total minutes.

**Downtime** means the web application or API returns errors (HTTP 5xx) or does not
respond for more than [5] consecutive minutes, as measured by Yugiverse's uptime checks
(published at [STATUS PAGE URL]).

Not Downtime:

- maintenance announced at least [48] hours ahead, up to [4] hours a month;
- problems caused by the Customer's connected cloud or SaaS providers, their APIs, billing
  export delays, or the Customer's network or identity provider;
- delays in background imports and analysis (they are not interactive availability);
- force majeure, or suspension under the Terms.

## 2. Service credits

| Monthly Uptime | Credit (% of that month's fee) |
|---|---|
| below [99.5 / 99.9]% | [10]% |
| below [99.0]% | [25]% |
| below [95.0]% | [50]% |

To claim, the account owner emails [SUPPORT EMAIL] within 30 days after the month ends.
Credits apply to future invoices, are not cash refunds, and are the Customer's only
remedy for Downtime. Total credits in a month are capped at [50]% of that month's fee.

## 3. Support response targets

| Severity | Example | First response |
|---|---|---|
| 1 — Critical | Service unavailable for all users | [4 business hours] |
| 2 — High | Major feature unusable, no workaround | [1 business day] |
| 3 — Normal | Question or minor problem | [2 business days] |

Business hours: [09:00–18:00 IST, Monday to Friday, excluding Indian public holidays].
