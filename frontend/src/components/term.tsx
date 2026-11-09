import { Link } from "react-router";
import { GLOSSARY } from "@/lib/glossary";

/** A word with its plain-English meaning on hover/focus, linking to the glossary. */
export function Term({ id, children }: { id: string; children?: React.ReactNode }) {
  const entry = GLOSSARY[id];
  if (!entry) return <>{children}</>;
  return (
    <Link to={`/glossary#${id}`} title={entry.short} aria-description={entry.short}
      className="cursor-help underline decoration-dotted underline-offset-4">
      {children ?? entry.term}
    </Link>
  );
}
