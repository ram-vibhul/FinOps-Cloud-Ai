import type { ReactNode } from "react";
import { ThemeToggle } from "@/components/theme-toggle";

/** Centered card layout for sign-in, invitations and onboarding. */
export function PublicLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 items-center justify-between px-4">
        <div className="flex items-center gap-2 font-semibold tracking-tight">
          <img src="/favicon.svg" alt="" className="size-7" />
          <span>CloudCostRadar</span>
        </div>
        <ThemeToggle />
      </header>
      <main id="main" className="flex flex-1 items-start justify-center px-4 py-8 sm:items-center">
        <div className="w-full max-w-md">{children}</div>
      </main>
    </div>
  );
}
