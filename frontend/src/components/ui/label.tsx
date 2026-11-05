import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Label({ className, children, ...props }: ComponentProps<"label">) {
  return (
    // oxlint-disable-next-line jsx-a11y/label-has-associated-control -- used with htmlFor
    <label data-slot="label" className={cn("text-sm font-medium leading-none", className)} {...props}>
      {children}
    </label>
  );
}
