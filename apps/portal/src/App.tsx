import { useRef, useState } from "react";

// Internal staff portal (§9.3): chat against audience=internal sessions
// (SSO header auth stub upstream), plus read-only transcript / feedback /
// audit / eval views wired to gateway read APIs in Phase 4.
interface Message {
  role: "user" | "assistant";
  text: string;
}

type Tab = "chat" | "transcripts" | "feedback" | "audit" | "evals";

export function App() {
  const sessionId = useRef(crypto.randomUUID());
  const [tab, setTab] = useState<Tab>("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", text: question }, { role: "assistant", text: "" }]);
    try {
      const resp = await fetch("/v1/chat", {
        method: "POST",
        // SSO header stub (§9.3): internal audience requires staff identity.
        headers: { "Content-Type": "application/json", "X-Staff-User": "dev-staff" },
        body: JSON.stringify({
          session_id: sessionId.current,
          brand: "etiqa",
          audience: "internal",
          message: question,
        }),
      });
      if (!resp.ok || !resp.body) throw new Error(String(resp.status));
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          const event = JSON.parse(frame.slice(6)) as { type: string; text?: string };
          if (event.type === "token" && event.text) {
            setMessages((prev) =>
              prev.map((m, i) => (i === prev.length - 1 ? { ...m, text: m.text + event.text } : m)),
            );
          }
        }
      }
    } catch {
      setMessages((prev) =>
        prev.map((m, i) => (i === prev.length - 1 ? { ...m, text: "Request failed." } : m)),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ fontFamily: "system-ui", maxWidth: 900, margin: "0 auto", padding: 16 }}>
      <h1>Staff portal</h1>
      <nav style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["chat", "transcripts", "feedback", "audit", "evals"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)} disabled={t === tab}>
            {t}
          </button>
        ))}
      </nav>
      {tab === "chat" ? (
        <div>
          <div style={{ minHeight: 300, border: "1px solid #ddd", padding: 12 }}>
            {messages.map((m, i) => (
              <p key={i}>
                <strong>{m.role}:</strong> {m.text}
              </p>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void send();
            }}
            style={{ display: "flex", gap: 8, marginTop: 8 }}
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              style={{ flex: 1, padding: 8 }}
              placeholder="Internal enquiry…"
            />
            <button type="submit" disabled={busy}>
              Send
            </button>
          </form>
        </div>
      ) : (
        <p>Read-only {tab} view arrives with the Phase 4 gateway read APIs.</p>
      )}
    </div>
  );
}
