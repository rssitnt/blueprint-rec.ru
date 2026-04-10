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
        "h-[100dvh] overflow-hidden text-ink",
        isWorkspaceRoute ? "bg-[#d7dade]" : "bg-[#111317]",
        headline.variable,
        body.variable
      )}
    >
      <div className={classNames("flex h-full w-full flex-col", isWorkspaceRoute ? "" : "px-3 py-3 md:px-4 md:py-4")}>
        <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
