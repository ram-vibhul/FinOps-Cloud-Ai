import { Sparkles } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type AskAnswer, errorMessage } from "@/lib/api";

/** "Ask in plain words": the AI proposes a report; nothing runs until the person applies it. */
export function AskBox({ labelOf, onApply }: { labelOf: (dim: string) => string; onApply: (a: AskAnswer) => void }) {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AskAnswer | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (question.trim().length < 3) return;
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      setAnswer(await api.askCosts(question.trim()));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const q = answer?.query;
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-1">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <div className="grid min-w-64 flex-1 gap-1">
            <Label htmlFor="ask-q">Ask in plain words</Label>
            <Input id="ask-q" value={question} maxLength={300} placeholder="e.g. EC2 cost by region for 3 months"
              onChange={(e) => setQuestion(e.target.value)} />
          </div>
          <Button type="submit" variant="outline" disabled={busy || question.trim().length < 3}>
            <Sparkles className="size-4" aria-hidden /> {busy ? "Thinking…" : "Suggest a report"}
          </Button>
        </form>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {answer && q && (
          <section aria-label="Suggested report" className="flex flex-col gap-2 rounded-md border p-3 text-sm">
            {answer.explanation && <p>{answer.explanation}</p>}
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-muted-foreground">
              <dt>Dates</dt><dd>{q.start} to {q.end}</dd>
              <dt>Group by</dt><dd>{q.group_by.map(labelOf).join(", then ")}</dd>
              <dt>Filters</dt>
              <dd>{Object.entries(q.filters).map(([d, v]) => `${labelOf(d)}: ${v.join(", ")}`).join("; ") || "None"}</dd>
              <dt>Show</dt><dd>{{ day: "By day", month: "By month", total: "Totals only" }[q.granularity]}, {q.metric === "billed" ? "billed" : "amortised"} cost</dd>
            </dl>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => { onApply(answer); setAnswer(null); }}>Use this report</Button>
              <Button size="sm" variant="ghost" onClick={() => setAnswer(null)}>Discard</Button>
            </div>
            <p className="text-xs text-muted-foreground">Suggested by AI. Check it before relying on the numbers.</p>
          </section>
        )}
      </CardContent>
    </Card>
  );
}
