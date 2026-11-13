import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";
import { DegradedBanner } from "@/components/DegradedBanner";

export const metadata: Metadata = {
  title: "GigBid",
  description: "Multi-agent freelancer copilot. Nothing reaches a client until you approve it.",
};
export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="skip">Skip to content</a>
        <Nav />
        <DegradedBanner />
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
