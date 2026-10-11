import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ErrorBox, JobProgress, Loading, Spinner, useToast } from "../components/ui";
import type { ChatMessage, Job } from "../types";
import { go, useAsync } from "../util";

const HINTS = [
  "Ile białka ma żona w środę?",
  "Zamień czwartkowy obiad na coś z rybą",
  "Gdzie w tym tygodniu jest feta?",
  "Zmniejsz mi kalorie o 100",
  "Ułóż nowy plan z większą ilością kuchni włoskiej",
];

function renderText(text: string) {
  // prosty Markdown: **pogrubienie**, _kursywa_ (tylko całe słowa — podkreślenia w identyfikatorach
  // typu kotwica_bialka_na_posilek zostają w spokoju)
  const parts = text.split(/(\*\*[^*]+\*\*|(?<=^|\s)_[^_\n]+_(?=[\s.,!?;:)]|$))/g);
  return parts.map((p, i) => {
    if (p === undefined) return null;
    if (p.startsWith("**") && p.endsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
    if (p.startsWith("_") && p.endsWith("_") && p.length > 2) return <i key={i}>{p.slice(1, -1)}</i>;
    return <span key={i}>{p}</span>;
  });
}

export default function ChatPage() {
  const toast = useToast();
  const history = useAsync<ChatMessage[]>(() => api.chatHistory(), []);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [resolved, setResolved] = useState<Record<string, string>>({});
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (history.data) setMessages(history.data);
  }, [history.data]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, sending]);

  const send = async (value?: string) => {
    const msg = (value ?? text).trim();
    if (!msg || sending) return;
    setText("");
    setMessages((m) => [...m, { id: Date.now(), role: "uzytkownik", text: msg, data: null }]);
    setSending(true);
    try {
      const reply = await api.chat(msg);
      setMessages((m) => [...m, reply]);
    } catch (e) {
      toast((e as Error).message, "error");
      setText((t) => t || msg); // nieudana wysyłka nie kasuje wpisanego tekstu
    } finally {
      setSending(false);
    }
  };

  const confirm = async (proposalId: string, accept: boolean) => {
    if (!accept) {
      setResolved((r) => ({ ...r, [proposalId]: "odrzucono" }));
      api.chatReject(proposalId).catch(() => undefined);
      return;
    }
    try {
      const reply = await api.chatConfirm(proposalId);
      setResolved((r) => ({ ...r, [proposalId]: "zapisano" }));
      setMessages((m) => [...m, reply]);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Czat</h1>
          <div className="sub">Pytaj o plan, proś o zamiany i zmiany założeń. Liczby zawsze podaje program, nie model językowy.</div>
        </div>
      </div>
      {history.error && <ErrorBox error={history.error} onRetry={history.reload} />}
      <div className="card chat">
        <div className="chat-log" ref={logRef}>
          {history.loading && !history.data && <Loading />}
          {messages.length === 0 && !history.loading && (
            <div className="empty">
              <div className="big">💬</div>
              <div>Napisz na przykład:</div>
              <div className="row" style={{ justifyContent: "center" }}>
                {HINTS.map((h) => (
                  <button key={h} className="btn small" onClick={() => send(h)}>
                    {h}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`msg ${m.role === "uzytkownik" ? "user" : "bot"}`}>
              {renderText(m.text)}
              {m.data?.job_id && <ChatJob jobId={m.data.job_id} />}
              {m.data?.proposal_id && (
                <div className="row" style={{ marginTop: 8 }}>
                  {resolved[m.data.proposal_id] ? (
                    <span className="badge">{resolved[m.data.proposal_id]}</span>
                  ) : (
                    <>
                      <button className="btn primary small" onClick={() => confirm(m.data!.proposal_id!, true)}>
                        {m.data.kind === "potwierdz_akcje" ? "Tak, uruchom" : "Zatwierdź zmiany"}
                      </button>
                      <button className="btn small" onClick={() => confirm(m.data!.proposal_id!, false)}>
                        Odrzuć
                      </button>
                    </>
                  )}
                </div>
              )}
              {m.data?.kind === "zalozenia_zapisane" && m.data.plan_id && (
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="btn small" onClick={() => go("tydzien", m.data!.plan_id!)}>Otwórz plan, żeby go przeliczyć</button>
                </div>
              )}
            </div>
          ))}
          {sending && (
            <div className="msg bot">
              <Spinner label="Myślę…" />
            </div>
          )}
        </div>
        <div className="chat-input">
          <textarea
            placeholder="Napisz wiadomość… (Enter wysyła, Shift+Enter nowa linia)"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button className="btn primary" onClick={() => send()} disabled={sending || !text.trim()}>
            Wyślij
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatJob({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [open, setOpen] = useState(true);
  return (
    <div style={{ marginTop: 8, whiteSpace: "normal" }}>
      {open && <JobProgress jobId={jobId} onDone={setJob} />}
      <div className="row" style={{ marginTop: 6 }}>
        {job?.status === "gotowe" && job.result?.plan_id && (
          <button className="btn small primary" onClick={() => go("tydzien", job.result!.plan_id!)}>
            Zobacz plan →
          </button>
        )}
        <button className="btn small ghost" onClick={() => setOpen((o) => !o)}>
          {open ? "Ukryj szczegóły" : "Pokaż szczegóły"}
        </button>
      </div>
    </div>
  );
}
