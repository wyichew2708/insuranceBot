import { useCallback, useRef, useState } from "react";

type Brand = "etiqa" | "tiq";

// Visual theming only — no brand facts (URLs/hotlines) live in code (§13).
const THEMES: Record<Brand, { primary: string; name: string }> = {
  tiq: { primary: "#00b0b9", name: "Tiq" },
  etiqa: { primary: "#ffd100", name: "Etiqa" },
};

interface ChatEvent {
  type: "token" | "citation" | "action" | "handover" | "done";
  text?: string;
  chunk_id?: string;
  title?: string;
  url?: string;
  action_id?: string;
  route?: string;
}

interface Message {
  role: "user" | "assistant";
  text: string;
  citations: ChatEvent[];
  actions: string[];
  feedback?: "up" | "down";
}

// Session lives in memory only — no localStorage (§2 conventions).
function newSessionId(): string {
  return crypto.randomUUID();
}

export function App({ brand, locale }: { brand: Brand; locale: string }) {
  const theme = THEMES[brand];
  const sessionId = useRef(newSessionId());
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const send = useCallback(async () => {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    const assistant: Message = { role: "assistant", text: "", citations: [], actions: [] };
    setMessages((prev) => [...prev, { role: "user", text: question, citations: [], actions: [] }, assistant]);

    const update = (fn: (m: Message) => Message) =>
      setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? fn(m) : m)));

    try {
      const resp = await fetch("/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId.current,
          brand,
          audience: "public",
          message: question,
        }),
      });
      if (!resp.ok || !resp.body) throw new Error(`chat failed: ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6)) as ChatEvent;
          if (event.type === "token" && event.text) {
            update((m) => ({ ...m, text: m.text + event.text }));
          } else if (event.type === "citation") {
            update((m) => ({ ...m, citations: [...m.citations, event] }));
          } else if (event.type === "action" && event.action_id) {
            update((m) => ({ ...m, actions: [...m.actions, event.action_id!] }));
          }
        }
      }
    } catch {
      update((m) => ({ ...m, text: m.text || "Sorry — something went wrong. Please try again." }));
    } finally {
      setBusy(false);
    }
  }, [input, busy, brand]);

  const rate = (index: number, feedback: "up" | "down") => {
    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, feedback } : m)));
    void fetch("/v1/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId.current, message_index: index, rating: feedback }),
    }).catch(() => undefined);
  };

  return (
    <div style={{ fontFamily: "system-ui", maxWidth: 420, border: "1px solid #ddd", borderRadius: 12 }}>
      <header style={{ background: theme.primary, padding: "10px 14px", borderRadius: "12px 12px 0 0" }}>
        <strong>{theme.name} Assistant</strong> <span lang={locale} />
      </header>
      <div style={{ height: 380, overflowY: "auto", padding: 12 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ margin: "8px 0", textAlign: m.role === "user" ? "right" : "left" }}>
            <div
              style={{
                display: "inline-block",
                padding: "8px 12px",
                borderRadius: 10,
                background: m.role === "user" ? theme.primary : "#f2f2f2",
                maxWidth: "85%",
              }}
            >
              {m.text || (busy && i === messages.length - 1 ? "…" : "")}
            </div>
            {m.citations.length > 0 && (
              <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
                {m.citations.map((c, j) => (
                  <div key={j}>
                    {c.url ? (
                      <a href={c.url} target="_blank" rel="noreferrer">
                        {c.title ?? c.url}
                      </a>
                    ) : (
                      <>from: {c.title ?? c.chunk_id}</>
                    )}
                  </div>
                ))}
              </div>
            )}
            {m.actions.map((actionId) => (
              // The renderer resolves action_id -> exact value via GET
              // /actions/{brand}/{id}; placeholder button in Phase 0.
              <button key={actionId} style={{ margin: "4px 4px 0 0" }} data-action-id={actionId}>
                {actionId}
              </button>
            ))}
            {m.role === "assistant" && m.text && (
              <div style={{ fontSize: 12, marginTop: 2 }}>
                <button onClick={() => rate(i, "up")} disabled={m.feedback !== undefined}>
                  👍
                </button>{" "}
                <button onClick={() => rate(i, "down")} disabled={m.feedback !== undefined}>
                  👎
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
        style={{ display: "flex", gap: 8, padding: 10, borderTop: "1px solid #eee" }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about your insurance…"
          style={{ flex: 1, padding: 8 }}
        />
        <button type="submit" disabled={busy}>
          Send
        </button>
      </form>
    </div>
  );
}
