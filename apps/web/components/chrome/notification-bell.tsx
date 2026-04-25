"use client";

import Link from "next/link";
import { useEventsStore } from "@/lib/events-store";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/cn";

const EVENT_LABELS: Record<string, string> = {
  price_shock: "Price shock",
  news_shock: "News",
  "8k_filed": "8-K filed",
  earnings_release: "Earnings",
  macro_surprise: "Macro",
};

function fmtRelative(ts: number): string {
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  return `${hrs}h ago`;
}

export function NotificationBell() {
  const unread = useEventsStore((s) => s.unread);
  const items = useEventsStore((s) => s.items);
  const markAllRead = useEventsStore((s) => s.markAllRead);
  const conn = useEventsStore((s) => s.conn);

  return (
    <Popover onOpenChange={(open) => open && markAllRead()}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label="Notifications"
          className="relative rounded px-2 py-1.5 text-white/80 transition-colors hover:bg-white/5"
        >
          <BellIcon />
          {unread > 0 && (
            <span
              className={cn(
                "absolute -right-0.5 -top-0.5 min-w-[16px] rounded-full px-1 text-[10px] font-semibold",
                "bg-red-500 text-white",
              )}
            >
              {unread > 99 ? "99+" : unread}
            </span>
          )}
          <span
            className={cn(
              "absolute bottom-0.5 right-0.5 h-1.5 w-1.5 rounded-full",
              conn === "open"
                ? "bg-green-400"
                : conn === "connecting" || conn === "reconnecting"
                  ? "bg-amber-400"
                  : "bg-red-500",
            )}
            aria-hidden
          />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between border-b border-fii-navy-100 px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-fii-mute">
            Notifications
          </p>
          <p className="text-[10px] text-fii-mute">
            {conn === "open" ? "Live" : conn}
          </p>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {items.length === 0 ? (
            <p className="px-3 py-6 text-center text-xs text-fii-mute">
              No notifications yet.
            </p>
          ) : (
            <ul className="divide-y divide-fii-navy-50">
              {items.map((it) => (
                <li key={it.key} className="px-3 py-2 hover:bg-fii-navy-50">
                  {it.kind === "analysis_updated" && it.analysis_id ? (
                    <Link
                      href={`/analysis/?id=${it.analysis_id}`}
                      className="block text-sm"
                    >
                      <p className="font-medium text-fii-navy">
                        {it.symbol} · analysis refreshed
                      </p>
                      <p className="text-[11px] text-fii-mute">
                        {EVENT_LABELS[it.event_type] ?? it.event_type} ·{" "}
                        {fmtRelative(it.receivedAt)}
                      </p>
                    </Link>
                  ) : (
                    <Link
                      href={`/stock/?s=${it.symbol}`}
                      className="block text-sm"
                    >
                      <p className="font-medium text-fii-navy">
                        {it.symbol} · {EVENT_LABELS[it.event_type] ?? it.event_type}
                        {it.watchlisted && (
                          <span className="ml-2 rounded bg-fii-blue/10 px-1.5 py-0.5 text-[10px] text-fii-blue-700">
                            watchlisted
                          </span>
                        )}
                      </p>
                      <p className="text-[11px] text-fii-mute">
                        {it.headline ?? "Tap to open"} · {fmtRelative(it.receivedAt)}
                      </p>
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function BellIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.8}
      stroke="currentColor"
      className="h-5 w-5"
      aria-hidden
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022c1.733.64 3.56 1.085 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0"
      />
    </svg>
  );
}
