"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import { NotificationBell } from "@/components/chrome/notification-bell";
import { useEventsStream } from "@/lib/events-sse";

const LINKS: { href: string; label: string }[] = [
  { href: "/", label: "Home" },
  { href: "/advisor/", label: "Advisor" },
  { href: "/history/", label: "History" },
  { href: "/watchlist/", label: "Watchlist" },
  { href: "/portfolio/", label: "Portfolio" },
  { href: "/feed/", label: "Feed" },
];

export function Nav() {
  const pathname = usePathname();
  useEventsStream();
  return (
    <header className="bg-fii-navy text-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <Link href="/" className="flex items-center gap-3">
          <div className="h-8 w-8 rounded-sm bg-fii-blue" aria-hidden />
          <span className="font-serif text-xl font-semibold tracking-tight">FII-PRISM</span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((l) => {
            const active = pathname === l.href;
            return (
              <Link
                key={l.href}
                href={l.href}
                className={cn(
                  "rounded px-3 py-1.5 transition-colors",
                  active ? "bg-white/10 text-white" : "text-white/70 hover:bg-white/5",
                )}
              >
                {l.label}
              </Link>
            );
          })}
          <NotificationBell />
        </nav>
      </div>
    </header>
  );
}
