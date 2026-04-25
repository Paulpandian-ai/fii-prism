import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/chrome/nav";
import { QueryProvider } from "@/components/chrome/query-provider";

export const metadata: Metadata = {
  title: "FII-PRISM — Factor Impact Intelligence",
  description:
    "Multi-agent research and decision-support for long-horizon fundamental investing. Educational use only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:wght@500;600;700&display=swap"
        />
      </head>
      <body>
        <QueryProvider>
          <Nav />
          {children}
        </QueryProvider>
      </body>
    </html>
  );
}
