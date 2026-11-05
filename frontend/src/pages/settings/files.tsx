import { FileUp } from "lucide-react";
import { type ChangeEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type FileImport, type ImportReport, type ImportSettings } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const REQUIRED: { key: string; label: string }[] = [
  { key: "charge_date", label: "Date" },
  { key: "service_name", label: "Service" },
  { key: "cost", label: "Cost" },
];
const OPTIONAL: { key: string; label: string }[] = [
  { key: "currency", label: "Currency" },
  { key: "sub_account_id", label: "Account" },
  { key: "region", label: "Region" },
  { key: "resource_id", label: "Resource" },
];

export function FilesSection() {
  const me = useMe();
  const canManage = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const uploads = useLoad(() => api.fileImports(), [me.active_organization_id]);
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="font-medium">Billing files</h3>
          <p className="text-sm text-muted-foreground">
            Upload a FOCUS export (Azure, Google Cloud, any provider) or any CSV with dates, services and costs.
          </p>
        </div>
        {canManage && <UploadDialog onDone={() => void uploads.reload()} />}
      </div>
      {(uploads.data?.length ?? 0) > 0 && (
        <Table>
          <THead><TR><TH>File</TH><TH>Format</TH><TH>Status</TH><TH>Rows</TH></TR></THead>
          <TBody>
            {(uploads.data ?? []).map((f) => (
              <TR key={f.id}>
                <TD className="max-w-xs truncate">{f.filename}</TD>
                <TD>{f.detected_format === "focus" ? "FOCUS" : "Custom CSV"}</TD>
                <TD>
                  <Badge variant={f.status === "failed" ? "destructive" : f.status === "imported" ? "default" : "secondary"}>
                    {f.status}
                  </Badge>
                </TD>
                <TD>{f.rows_imported}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </section>
  );
}

function ReportView({ report }: { report: ImportReport }) {
  return (
    <div className="grid gap-2 rounded-lg border p-3 text-sm" aria-live="polite">
      <p>
        {report.rows_valid} of {report.rows_read} rows are valid
        {report.first_date && ` (${report.first_date} to ${report.last_date})`}.
      </p>
      <p>Total: {Object.entries(report.totals).map(([c, v]) => `${v} ${c}`).join(", ") || "—"}</p>
      {report.error_count > 0 && (
        <div>
          <p className="font-medium text-destructive">{report.error_count} row(s) have problems:</p>
          <ul className="max-h-32 overflow-y-auto text-xs">
            {report.errors.map((e) => (
              <li key={e.row}>Row {e.row}: {e.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function UploadDialog({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [upload, setUpload] = useState<FileImport | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [currency, setCurrency] = useState("USD");
  const [dateFormat, setDateFormat] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setUpload(null);
    setReport(null);
    setMapping({});
  }

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      const created = await api.uploadFile(file);
      setUpload(created);
      setMapping(Object.fromEntries(Object.entries(created.mapping).filter(([, v]) => typeof v === "string")) as Record<string, string>);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const settings = (): ImportSettings =>
    upload?.detected_format === "focus"
      ? {}
      : { mapping, currency: mapping.currency ? undefined : currency, date_format: dateFormat || undefined, provider: "other" };

  async function validate() {
    if (!upload) return;
    try {
      setReport(await api.validateFile(upload.id, settings()));
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function runImport() {
    if (!upload) return;
    try {
      await api.importFile(upload.id, { ...settings(), allow_partial: (report?.error_count ?? 0) > 0 });
      toast.success("Import started. Costs appear when it finishes.");
      setOpen(false);
      reset();
      onDone();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) reset(); }}>
      <DialogTrigger asChild>
        <Button variant="outline"><FileUp /> Upload file</Button>
      </DialogTrigger>
      <DialogContent title="Import a billing file" description="CSV or Parquet, up to 500 MB.">
        {!upload ? (
          <div className="grid gap-2">
            <Label htmlFor="billing-file">File</Label>
            <Input id="billing-file" type="file" accept=".csv,.parquet,text/csv" onChange={(e) => void onFile(e)} disabled={busy} />
          </div>
        ) : (
          <div className="grid gap-3">
            <p className="text-sm">
              <span className="font-medium">{upload.filename}</span> —{" "}
              {upload.detected_format === "focus" ? "FOCUS format detected; columns are mapped automatically." : "Match its columns:"}
            </p>
            {upload.detected_format === "custom" && (
              <div className="grid gap-2 sm:grid-cols-2">
                {[...REQUIRED, ...OPTIONAL].map((f) => (
                  <div key={f.key} className="grid gap-1">
                    <Label htmlFor={`map-${f.key}`}>{f.label}{REQUIRED.includes(f) ? " *" : ""}</Label>
                    <NativeSelect id={`map-${f.key}`} value={mapping[f.key] ?? ""}
                      onChange={(e) => setMapping({ ...mapping, [f.key]: e.target.value })}>
                      <option value="">—</option>
                      {upload.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                    </NativeSelect>
                  </div>
                ))}
                {!mapping.currency && (
                  <div className="grid gap-1">
                    <Label htmlFor="default-currency">Currency of all rows</Label>
                    <Input id="default-currency" value={currency} maxLength={3}
                      onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
                  </div>
                )}
                <div className="grid gap-1">
                  <Label htmlFor="date-format">Date format (if not YYYY-MM-DD)</Label>
                  <Input id="date-format" placeholder="%d/%m/%Y" value={dateFormat} onChange={(e) => setDateFormat(e.target.value)} />
                </div>
              </div>
            )}
            {report && <ReportView report={report} />}
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => void validate()}>Check file</Button>
              <Button onClick={() => void runImport()} disabled={!report || report.rows_valid === 0}>Import</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
