import { useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { type Language, LANGUAGES, language, setLanguage, t } from "@/lib/i18n";

/** The viewer's interface language (11.37): kept on this browser only. */
export function LanguageCard() {
  const [value, setValue] = useState<Language>(language());
  const draft = LANGUAGES.find((l) => l.value === value)?.draft;
  if (LANGUAGES.length < 2) return null; // nothing to choose yet
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("Language")}</CardTitle>
        <CardDescription>
          The language of menus and labels on this browser. Translations are being added; anything not translated yet
          stays in English.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid max-w-sm gap-2">
        <Label htmlFor="language">Language</Label>
        <NativeSelect
          id="language"
          value={value}
          onChange={(e) => {
            const next = e.target.value as Language;
            setLanguage(next);
            setValue(next);
            toast.success(t("Saved. Pages you open now use it."));
            window.setTimeout(() => window.location.reload(), 600);
          }}
        >
          {LANGUAGES.map((l) => <option key={l.value} value={l.value}>{l.label}{l.draft ? " (draft)" : ""}</option>)}
        </NativeSelect>
        {draft && <p className="text-sm text-muted-foreground">A first draft covering the navigation; tell us about wording that reads oddly.</p>}
      </CardContent>
    </Card>
  );
}
