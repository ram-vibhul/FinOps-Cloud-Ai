import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Table({ className, ...props }: ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto rounded-lg border">
      <table className={cn("w-full caption-bottom text-sm", className)} {...props} />
    </div>
  );
}
export const THead = (p: ComponentProps<"thead">) => <thead className="bg-muted/50 text-left" {...p} />;
export const TBody = (p: ComponentProps<"tbody">) => <tbody className="divide-y" {...p} />;
export const TR = (p: ComponentProps<"tr">) => <tr {...p} />;
export function TH({ className, ...props }: ComponentProps<"th">) {
  return <th className={cn("px-3 py-2 font-medium text-muted-foreground", className)} {...props} />;
}
export function TD({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("px-3 py-2 align-middle", className)} {...props} />;
}
