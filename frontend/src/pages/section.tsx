import { EmptyState } from "@/components/empty-state";
import type { NavItem } from "@/app/nav";
import { t } from "@/lib/i18n";

/** Placeholder for sections not yet built. */
export function SectionPage({ item }: { item: NavItem }) {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t(item.label)}</h1>
        <p className="text-sm text-muted-foreground">{t(item.description)}</p>
      </div>
      <EmptyState icon={item.icon} title="Coming soon">
        This part of CloudCostRadar is being built.
      </EmptyState>
    </div>
  );
}
