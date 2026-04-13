 "use client";

import { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { Manrope, Space_Grotesk } from "next/font/google";
import { classNames } from "../ui/utils";

const headline = Space_Grotesk({
  subsets: ["latin"],
  weight: ["500", "700"],
  variable: "--font-display"
});

const body = Manrope({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body"
});

export function SiteShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const isWorkspaceRoute = pathname?.startsWith("/sessions/");

  return (
    <div
      className={classNames(
        "text-ink",
        isWorkspaceRoute ? "h-[100dvh] overflow-hidden" : "min-h-[100dvh] overflow-y-auto",
        isWorkspaceRoute ? "bg-[#d7dade]" : "bg-[#13100d]",
        headline.variable,
        body.variable
      )}
    >
      <div className={classNames("flex h-full w-full flex-col", isWorkspaceRoute ? "" : "px-3 py-3 md:px-5 md:py-5")}>
        <main className={classNames("flex-1", isWorkspaceRoute ? "min-h-0 overflow-hidden" : "")}>{children}</main>
      </div>
    </div>
  );
}
