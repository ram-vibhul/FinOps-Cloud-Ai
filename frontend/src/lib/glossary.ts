/** Plain-English meanings of the cloud and FinOps words CostLens uses (9.2). */
export interface GlossaryEntry { term: string; short: string; long?: string }

export const GLOSSARY: Record<string, GlossaryEntry> = {
  amortised: {
    term: "Amortised cost",
    short: "Cost with up-front payments spread over the months they cover.",
    long: "If you pay for a one-year commitment in advance, the bill shows it all in one month. Amortised cost spreads it evenly over the year, so each month shows what it really used. CostLens shows amortised cost unless you choose billed cost.",
  },
  billed: { term: "Billed cost", short: "What appears on the invoice for the month, including up-front payments on the day they were charged." },
  effective: { term: "Effective cost", short: "Another name for amortised cost: what you really paid for each day's use after discounts." },
  on_demand: { term: "On-demand", short: "Paying the list price by the hour or second, with no commitment. The most flexible and the most expensive way to buy." },
  savings_plan: {
    term: "Savings Plan",
    short: "A promise to spend a fixed amount per hour for one or three years, in exchange for a discount of up to about 70%.",
    long: "AWS applies the discount automatically to matching usage. If you use less than you promised, you still pay the promised amount.",
  },
  reservation: { term: "Reserved Instance (reservation)", short: "A discount for promising to use a specific kind of server or database for one or three years." },
  coverage: { term: "Commitment coverage", short: "How much of the usage that could be discounted is covered by Savings Plans or reservations. Higher is cheaper, up to a point." },
  utilization: { term: "Commitment utilisation", short: "How much of what you committed to was actually used. Below 100% means paying for something you did not use." },
  credits: { term: "Credits", short: "Money the cloud provider gives back (promotions, refunds, support credits). Shown as negative cost." },
  forecast: { term: "Forecast", short: "Where this month's spend is heading, from the recent daily rate and costs that repeat each month." },
  anomaly: { term: "Unusual cost increase (anomaly)", short: "A day when something cost clearly more than its normal pattern, allowing for weekdays and weekends." },
  showback: { term: "Showback", short: "Showing each team what its cloud use cost, without moving money between budgets." },
  chargeback: { term: "Chargeback", short: "Actually charging each team or customer for its share of the cloud bill." },
  shared_cost: { term: "Shared costs", short: "Costs that serve many teams (networking, support, security tools) and are split between them by a rule." },
  unit_cost: { term: "Unit cost", short: "Cloud cost divided by something the business counts, such as orders or active users. It shows whether growth is getting more efficient." },
  tag: { term: "Tag", short: "A label on a cloud resource (for example team = payments). Tags let costs be grouped by team, project or environment." },
  virtual_tag: { term: "Virtual tag", short: "A label CostLens works out from rules, for costs that were never tagged in the cloud." },
  rightsizing: { term: "Right-sizing", short: "Moving a server or database to a smaller size that still fits how much it is really used." },
  idle: { term: "Idle resource", short: "Something you pay for that does little or no work: a stopped server's disks, an unused IP address, an empty load balancer." },
  spot: { term: "Spot", short: "Spare cloud capacity sold at a deep discount that the provider can take back at short notice. Good for work that can be interrupted." },
  graviton: { term: "Graviton (Arm)", short: "AWS's own processors. Usually about 20% cheaper for the same work, if the software runs on Arm." },
  egress: { term: "Data transfer (egress)", short: "Charges for data leaving a region or the cloud. Often a surprise on the bill." },
  focus: { term: "FOCUS", short: "An open standard format for cloud bills, so costs from different providers can be compared line by line." },
  list_price: { term: "List price", short: "The published price before any discount you have negotiated or committed to." },
  margin: { term: "Margin (partners)", short: "What a partner keeps: the re-billed amount minus the cloud cost. Never shown to clients." },
};

export const GLOSSARY_KEYS = Object.keys(GLOSSARY) as (keyof typeof GLOSSARY)[];
