import { useSearchParams } from "react-router";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AllocationTab } from "./allocation";
import { BudgetsTab } from "./budgets";
import { PoliciesTab } from "./policies";
import { ReportsTab } from "./reports";
import { StatementsTab } from "./statements";
import { UnitCostsTab } from "./unit-costs";

export function GovernancePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "budgets";
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Governance</h1>
        <p className="text-sm text-muted-foreground">Budgets, policies, allocation and reports.</p>
      </div>
      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v }, { replace: true })}>
        <TabsList>
          <TabsTrigger value="budgets">Budgets</TabsTrigger>
          <TabsTrigger value="policies">Policies</TabsTrigger>
          <TabsTrigger value="allocation">Allocation</TabsTrigger>
          <TabsTrigger value="statements">Statements</TabsTrigger>
          <TabsTrigger value="reports">Reports</TabsTrigger>
          <TabsTrigger value="unit-costs">Unit costs</TabsTrigger>
        </TabsList>
        <TabsContent value="budgets" className="mt-4"><BudgetsTab /></TabsContent>
        <TabsContent value="policies" className="mt-4"><PoliciesTab /></TabsContent>
        <TabsContent value="allocation" className="mt-4"><AllocationTab /></TabsContent>
        <TabsContent value="statements" className="mt-4"><StatementsTab /></TabsContent>
        <TabsContent value="reports" className="mt-4"><ReportsTab /></TabsContent>
        <TabsContent value="unit-costs" className="mt-4"><UnitCostsTab /></TabsContent>
      </Tabs>
    </div>
  );
}
