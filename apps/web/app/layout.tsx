import type { Metadata } from "next";
import "./globals.css";
import { SiteShell } from "../components/layout/site-shell";

export const metadata: Metadata = {
  title: "Blueprint Annotation Desk",
  description: "Shared annotation workspace for AI and human point placement on drawings.",
  icons: {
    icon: "/favicon.ico",
    shortcut: "/favicon.ico"
  }
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <SiteShell>{children}</SiteShell>
      </body>
    </html>
  );
}
