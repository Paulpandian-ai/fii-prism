"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Disclaimer } from "@/components/chrome/disclaimer";
import {
  useChatMessages,
  useChatSessions,
  useCreateChatSession,
  useDeleteChatSession,
} from "@/lib/api";
import { streamChatMessage } from "@/lib/chat-stream";
import { fmtRelative } from "@/lib/format";
import type { ChatMessageItem, ChatStreamFrame, ChatToolCall } from "@/lib/types";

const TOOL_LABELS: Record<string, string> = {
  get_my_portfolio: "Reading your portfolio",
  get_my_watchlist: "Loading your watchlist",
  get_latest_analysis: "Fetching latest analysis",
  run_quick_analysis: "Running a fresh quick refresh",
  get_peer_comparison: "Comparing peer tickers",
  calculate_tax_loss_harvest: "Calculating tax consequences",
  calculate_portfolio_impact: "Simulating portfolio impact",
};

interface InflightAssistant {
  text: string;
  toolCalls: ChatToolCall[];
  cost?: number;
  done: boolean;
  error?: string;
}

export default function AdvisorPage() {
  const sessions = useChatSessions();
  const createSession = useCreateChatSession();
  const deleteSession = useDeleteChatSession();
  const [activeId, setActiveId] = useState<string | null>(null);
  const messages = useChatMessages(activeId);
  const [inflight, setInflight] = useState<InflightAssistant | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollerRef = useRef<HTMLDivElement | null>(null);

  // Pick the first session by default; create one if none exist.
  useEffect(() => {
    if (activeId) return;
    if (sessions.data && sessions.data.length > 0) {
      const first = sessions.data[0];
      if (first) setActiveId(first.session_id);
    }
  }, [sessions.data, activeId]);

  // Auto-scroll on new content.
  useEffect(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.data, inflight]);

  async function handleNewSession() {
    const r = await createSession.mutateAsync();
    setActiveId(r.session_id);
    setInflight(null);
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this chat session?")) return;
    await deleteSession.mutateAsync(id);
    if (id === activeId) setActiveId(null);
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    const content = input.trim();
    if (!content) return;
    let sessionId = activeId;
    if (!sessionId) {
      const r = await createSession.mutateAsync();
      sessionId = r.session_id;
      setActiveId(sessionId);
    }
    setInput("");
    setBusy(true);
    setInflight({ text: "", toolCalls: [], done: false });
    try {
      for await (const frame of streamChatMessage(sessionId, content)) {
        applyFrame(frame, setInflight);
        if (frame.kind === "message_complete") {
          // Re-fetch the persisted message list once the assistant turn is done.
          await messages.refetch();
          await sessions.refetch();
        }
      }
    } catch (err) {
      setInflight((prev) => ({
        text: prev?.text ?? "",
        toolCalls: prev?.toolCalls ?? [],
        done: true,
        error: (err as Error).message,
      }));
    } finally {
      setBusy(false);
      // Clear the inflight buffer once persisted messages catch up.
      setTimeout(() => setInflight(null), 200);
    }
  }

  return (
    <main className="mx-auto grid h-[calc(100vh-72px)] max-w-7xl grid-cols-1 gap-4 px-4 py-4 lg:grid-cols-[280px_1fr]">
      {/* Sessions sidebar */}
      <aside className="flex h-full flex-col rounded-lg border border-fii-navy-100 bg-white">
        <div className="flex items-center justify-between border-b border-fii-navy-100 px-3 py-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-fii-mute">
            Conversations
          </p>
          <Button size="sm" onClick={handleNewSession} disabled={createSession.isPending}>
            New
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {(sessions.data ?? []).length === 0 ? (
            <p className="px-3 py-4 text-xs text-fii-mute">
              No chats yet. Start one with a question below.
            </p>
          ) : (
            <ul className="divide-y divide-fii-navy-50">
              {(sessions.data ?? []).map((sess) => {
                const active = sess.session_id === activeId;
                return (
                  <li
                    key={sess.session_id}
                    className={
                      "group flex items-start justify-between gap-2 px-3 py-2 hover:bg-fii-navy-50 " +
                      (active ? "bg-fii-navy-50" : "")
                    }
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => setActiveId(sess.session_id)}
                    >
                      <p className="truncate text-sm font-medium text-fii-navy">
                        {sess.title}
                      </p>
                      <p className="text-[11px] text-fii-mute">
                        {sess.message_count} msg · ${sess.total_cost_usd.toFixed(4)} ·{" "}
                        {fmtRelative(sess.updated_at)}
                      </p>
                    </button>
                    <button
                      type="button"
                      className="text-[11px] text-fii-mute opacity-0 group-hover:opacity-100 hover:text-red-600"
                      onClick={() => handleDelete(sess.session_id)}
                      aria-label="Delete chat"
                    >
                      ✕
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </aside>

      {/* Conversation panel */}
      <section className="flex h-full flex-col rounded-lg border border-fii-navy-100 bg-white">
        <header className="border-b border-fii-navy-100 px-5 py-3">
          <h1 className="font-serif text-lg font-semibold text-fii-navy">Wealth Advisor</h1>
          <p className="text-xs text-fii-mute">
            Conversational agent over your portfolio + analysis history. Educational only.
          </p>
        </header>

        <div ref={scrollerRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {(messages.data ?? []).length === 0 && !inflight && (
            <Card>
              <CardContent className="py-8 text-center text-sm text-fii-mute">
                Ask about a position you hold (&quot;Can I keep XOM?&quot;), a candidate
                (&quot;Should I buy NVDA at the current price?&quot;), or peer alternatives
                (&quot;What are better holdings than INTC in semis?&quot;).
              </CardContent>
            </Card>
          )}
          {(messages.data ?? []).map((m) => (
            <MessageRow key={m.message_id} msg={m} />
          ))}
          {inflight && <InflightRow data={inflight} />}
        </div>

        <form
          onSubmit={handleSend}
          className="flex items-end gap-3 border-t border-fii-navy-100 px-5 py-3"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your portfolio…"
            rows={2}
            className="flex-1 resize-none rounded-md border border-fii-navy-100 px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fii-blue/40"
            disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!busy) void handleSend(e);
              }
            }}
          />
          <Button type="submit" disabled={busy || !input.trim()}>
            {busy ? "Thinking…" : "Send"}
          </Button>
        </form>
      </section>
      <div className="lg:col-span-2">
        <Disclaimer />
      </div>
    </main>
  );
}

function MessageRow({ msg }: { msg: ChatMessageItem }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-fii-blue px-4 py-2 text-sm text-white">
          {msg.content}
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {(msg.tool_calls ?? []).map((tc, i) => (
        <ToolBadge key={`${msg.message_id}-${i}`} tc={tc} />
      ))}
      <Card>
        <CardContent className="space-y-3 py-3">
          <div className="whitespace-pre-wrap text-sm text-fii-navy">
            {msg.content}
          </div>
          <Citations ids={msg.referenced_analysis_ids} cost={msg.cost_usd} />
        </CardContent>
      </Card>
    </div>
  );
}

function InflightRow({ data }: { data: InflightAssistant }) {
  return (
    <div className="space-y-2">
      {data.toolCalls.map((tc, i) => (
        <ToolBadge key={`inflight-${i}`} tc={tc} />
      ))}
      <Card>
        <CardContent className="space-y-3 py-3">
          {data.text ? (
            <div className="whitespace-pre-wrap text-sm text-fii-navy">{data.text}</div>
          ) : (
            <p className="text-sm text-fii-mute">Thinking…</p>
          )}
          {data.error && <p className="text-sm text-red-600">{data.error}</p>}
        </CardContent>
      </Card>
    </div>
  );
}

function ToolBadge({ tc }: { tc: ChatToolCall }) {
  const label = TOOL_LABELS[tc.name] ?? tc.name;
  const ran = tc.result !== null && tc.result !== undefined;
  return (
    <div className="flex items-center gap-2 text-[11px] text-fii-mute">
      <span
        className={
          "inline-block h-2 w-2 rounded-full " +
          (ran ? "bg-emerald-500" : "bg-amber-400 animate-pulse")
        }
        aria-hidden
      />
      <span>{label}</span>
      {tc.input && Object.keys(tc.input).length > 0 && (
        <code className="rounded bg-fii-navy-50 px-1.5 py-0.5 text-fii-navy">
          {compactInput(tc.input)}
        </code>
      )}
    </div>
  );
}

function Citations({ ids, cost }: { ids: string[]; cost: number }) {
  const unique = useMemo(() => Array.from(new Set(ids)), [ids]);
  if (unique.length === 0 && cost === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-fii-navy-50 pt-2 text-[11px] text-fii-mute">
      {unique.length > 0 && (
        <>
          <span>Sources:</span>
          {unique.map((id) => (
            <Link
              key={id}
              href={`/analysis/?id=${id}`}
              className="rounded bg-fii-blue/10 px-1.5 py-0.5 text-fii-blue-700 hover:bg-fii-blue/20"
            >
              {id.slice(0, 8)}
            </Link>
          ))}
        </>
      )}
      {cost > 0 && <span className="ml-auto">${cost.toFixed(4)}</span>}
    </div>
  );
}

function compactInput(input: Record<string, unknown>): string {
  const entries = Object.entries(input).slice(0, 2);
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" ");
}

function applyFrame(
  frame: ChatStreamFrame,
  setInflight: React.Dispatch<React.SetStateAction<InflightAssistant | null>>,
) {
  setInflight((prev) => {
    const base: InflightAssistant = prev ?? { text: "", toolCalls: [], done: false };
    if (frame.kind === "tool_use_started") {
      return {
        ...base,
        toolCalls: [
          ...base.toolCalls,
          { name: frame.tool_name, input: frame.tool_input, result: null },
        ],
      };
    }
    if (frame.kind === "tool_result") {
      const updated = [...base.toolCalls];
      for (let i = updated.length - 1; i >= 0; i -= 1) {
        const candidate = updated[i];
        if (candidate && candidate.name === frame.tool_name && candidate.result == null) {
          updated[i] = { ...candidate, result: frame.tool_result };
          break;
        }
      }
      return { ...base, toolCalls: updated };
    }
    if (frame.kind === "message_complete") {
      return {
        ...base,
        text: frame.final_text,
        cost: frame.cost_usd,
        done: true,
      };
    }
    if (frame.kind === "error") {
      return { ...base, done: true, error: frame.text };
    }
    return base;
  });
}
