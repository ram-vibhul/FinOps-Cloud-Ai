import { useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { money, NUMBER_FORMATS, numberLocale, setNumberLocale } from "@/lib/format";

/** The viewer's number format (11.37): kept on this browser only. */
export function NumberFormatCard() {
  const [value, setValue] = useState(numberLocale() ?? "");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Number format</CardTitle>
        <CardDescription>How amounts are written for you on this browser. Other people keep their own choice.</CardDescription>
      </CardHeader>
      <CardContent className="grid max-w-sm gap-2">
        <Label htmlFor="number-format">Format</Label>
        <NativeSelect
          id="number-format"
          value={value}
          onChange={(e) => {
            setNumberLocale(e.target.value);
            setValue(e.target.value);
            toast.success("Saved. Pages you open now use it.");
          }}
        >
          {NUMBER_FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </NativeSelect>
        <p className="text-sm text-muted-foreground">
          For example {money(1234567, "INR")} and {money(1234567, "INR", { compact: true })}
        </p>
      </CardContent>
    </Card>
  );
}
