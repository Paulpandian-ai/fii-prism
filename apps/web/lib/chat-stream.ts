/**
 * Stream the assistant turn from POST /chat/{id}/message.
 *
 * EventSource only supports GET, so we fetch() with a streaming body and parse
 * the `data:` lines ourselves. The async iterator yields ChatStreamFrame objects
 * suitable for the chat store's reducer.
 */

import { chatStreamUrl } from "./api";
import type { ChatStreamFrame } from "./types";

export async function* streamChatMessage(
  sessionId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamFrame> {
  const res = await fetch(chatStreamUrl(sessionId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`Chat stream failed: ${res.status} ${text.slice(0, 200)}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by blank lines.
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const dataLines = raw
          .split("\n")
          .filter((l) => l.startsWith("data: "))
          .map((l) => l.slice(6));
        if (dataLines.length === 0) continue;
        try {
          const parsed = JSON.parse(dataLines.join("\n")) as ChatStreamFrame;
          yield parsed;
        } catch {
          // Skip malformed frames — heartbeats are SSE comments and won't reach here.
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
