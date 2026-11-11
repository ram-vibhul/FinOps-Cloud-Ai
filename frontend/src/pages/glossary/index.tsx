import { useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { GLOSSARY } from "@/lib/glossary";

/** Plain-English glossary for finance people (9.2). */
export function GlossaryPage() {
  const [q, setQ] = useState("");
  const needle = q.trim().toLowerCase();
  const entries = Object.entries(GLOSSARY)
    .filter(([, e]) => !needle || `${e.term} ${e.short} ${e.long ?? ""}`.toLowerCase().includes(needle))
    .toSorted(([, a], [, b]) => a.term.localeCompare(b.term));
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Glossary</h1>
        <p className="text-sm text-muted-foreground">The cloud and FinOps words CloudCostRadar uses, in plain English.</p>
      </div>
      <Input aria-label="Search the glossary" placeholder="Search, e.g. amortised" value={q} onChange={(e) => setQ(e.target.value)} />
      <Card>
        <CardContent className="pt-1">
          <dl className="grid gap-4">
            {entries.map(([id, e]) => (
              <div key={id} id={id} className="scroll-mt-20">
                <dt className="font-medium">{e.term}</dt>
                <dd className="text-sm text-muted-foreground">{e.short}{e.long ? <span className="mt-1 block">{e.long}</span> : null}</dd>
              </div>
            ))}
            {entries.length === 0 && <p className="text-sm text-muted-foreground">No matching words.</p>}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
