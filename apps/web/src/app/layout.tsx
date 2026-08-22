import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LCA Auto Research",
  description:
    "自主科研 Agent：把研究目标和材料，推进成可复核的 LCA 研究结果或边界清楚的受限结果。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" className="h-full antialiased">
      <body className="min-h-full bg-background text-foreground">{children}</body>
    </html>
  );
}
