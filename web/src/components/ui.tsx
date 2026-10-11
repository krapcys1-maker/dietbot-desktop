import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, followJob } from "../api";
import type { BandState, Job } from "../types";
import { TAG_NAMES } from "../util";

// ---------------------------------------------------------------- powiadomienia
type Toast = { id: number; text: string; kind: "info" | "error" };
const ToastCtx = createContext<(text: string, kind?: "info" | "error") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "info" | "error" = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-4), { id, text, kind }]);
    // błędy zostają, aż je zamkniesz (wcześniej znikały po 7 s, zanim dało się je przeczytać — audyt S13)
    if (kind !== "error") setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);
  const close = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-wrap" aria-live="polite" aria-atomic="false">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`} role={t.kind === "error" ? "alert" : undefined}>
            <span className="grow">{t.text}</span>
            {t.kind === "error" && <button className="toast-close" aria-label="Zamknij komunikat" onClick={() => close(t.id)}>✕</button>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- drobne elementy
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row muted">
      <span className="spinner" /> {label}
    </span>
  );
}

export function Loading({ label = "Wczytywanie…" }: { label?: string }) {
  return (
    <div className="empty">
      <Spinner label={label} />
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="alert error row" role="alert">
      <span className="grow">{error}</span>
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Spróbuj ponownie
        </button>
      )}
    </div>
  );
}

export function TagBadges({ tags, isTreat, isSweet, diets }: { tags: string[]; isTreat?: boolean; isSweet?: boolean; diets?: string[] }) {
  // „wege”/„wegan” pokazujemy tylko z obliczonej zgodności składników — sam tag w danych bywał mylący (np. danie z parmezanem)
  const shown = tags.filter((t) => TAG_NAMES[t] && t !== "wege" && t !== "wegan");
  return (
    <span className="row" style={{ gap: 5 }}>
      {diets?.includes("wegan") ? <span className="badge ok">wegańskie</span>
        : diets?.includes("wege") ? <span className="badge ok">wegetariańskie</span> : null}
      {(isTreat || tags.includes("pula_20")) && <span className="badge treat">pula 20%</span>}
      {(isSweet || tags.includes("slodkie")) && <span className="badge sweet">słodkie</span>}
      {tags.includes("na_zapas") && <span className="badge batch">na zapas</span>}
      {shown
        .filter((t) => !["pula_20", "slodkie", "na_zapas"].includes(t))
        .map((t) => (
          <span key={t} className="badge">
            {TAG_NAMES[t]}
          </span>
        ))}
    </span>
  );
}

export function StateBadge({ state }: { state: BandState | null }) {
  if (!state) return null;
  if (state === "ok") return <span className="badge ok">✓</span>;
  return <span className="badge warn">{state === "za_malo" ? "za mało" : "za dużo"}</span>;
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Okno dialogowe: fokus wchodzi do okna i nie wychodzi z niego klawiszem Tab, po zamknięciu wraca na przycisk,
 *  który je otworzył. `confirmClose` — pytanie przed zamknięciem (np. niezapisany formularz). */
export function Modal({ title, onClose, children, footer, wide, confirmClose }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean; confirmClose?: () => boolean;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const requestClose = useCallback(() => {
    if (!confirmClose || confirmClose()) onClose();
  }, [onClose, confirmClose]);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = dialogRef.current?.querySelector<HTMLElement>(".dialog-body " + FOCUSABLE) ?? dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => opener?.focus?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); requestClose(); return; }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const [firstItem, lastItem] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === firstItem) { e.preventDefault(); lastItem.focus(); }
      else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); firstItem.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [requestClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && requestClose()}>
      <div ref={dialogRef} className="dialog" style={wide ? { width: "min(980px, 100%)" } : undefined} role="dialog" aria-modal="true" aria-labelledby="dialog-title">
        <div className="dialog-head">
          <h2 id="dialog-title">{title}</h2>
          <button className="btn ghost" onClick={requestClose} aria-label="Zamknij">
            ✕
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- postęp zadania w tle
export function JobProgress({ jobId, onDone }: { jobId: string; onDone?: (job: Job) => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => {
    const ctrl = new AbortController();
    setLines([]);
    followJob(
      jobId,
      (newLines, j) => {
        setLines((l) => [...l, ...newLines]);
        setJob(j);
      },
      ctrl.signal,
    )
      .then((j) => {
        setJob(j);
        doneRef.current?.(j);
      })
      .catch((e: Error & { status?: number }) => {
        if (ctrl.signal.aborted) return;
        // zadania żyją w pamięci aplikacji — po ponownym uruchomieniu stare już nie istnieją
        const message = e.status === 404 ? "To zadanie było w poprzedniej sesji aplikacji — wynik jest w planach." : e.message;
        setError(message);
        // strona nadrzędna też musi wiedzieć, że śledzenie się skończyło (inaczej przyciski zostają wyłączone na stałe)
        doneRef.current?.({ id: jobId, kind: "", title: "", status: "blad", log: [], log_size: 0, result: null,
          error: message, created_at: new Date().toISOString(), finished_at: null });
      });
    return () => ctrl.abort();
  }, [jobId]);

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines]);

  const running = !error && (!job || job.status === "w_toku" || job.status === "w_kolejce");
  const [stopping, setStopping] = useState(false);
  const stop = () => {
    setStopping(true);
    api.cancelJob(jobId).catch(() => setStopping(false));
  };
  return (
    <div className="stack">
      <div className="row">
        {running ? <Spinner label={stopping ? "Przerywam…" : job?.status === "w_kolejce" ? "Czeka w kolejce…" : "Liczę…"} /> : null}
        {running && <button className="btn small ghost" disabled={stopping} onClick={stop}>Przerwij</button>}
        {job?.status === "gotowe" && <span className="badge ok">Gotowe</span>}
        {job?.status === "blad" && <span className="badge error">Błąd</span>}
        {job?.status === "przerwane" && <span className="badge warn">Przerwano</span>}
      </div>
      <div className="joblog" ref={boxRef}>
        {lines.length === 0 && <div>Start…</div>}
        {lines.map((l, i) => (
          <div key={i} className={/poprawny|Gotowe|w widełkach/.test(l) ? "line-ok" : /błęd|Uwaga|poza|niedostępny/i.test(l) ? "line-warn" : ""}>
            {l}
          </div>
        ))}
      </div>
      {job?.error && <div className={`alert ${job.status === "przerwane" ? "warn" : "error"}`}>{job.error}</div>}
      {error && <div className={`alert ${error.startsWith("To zadanie było") ? "info" : "error"}`}>{error}</div>}
    </div>
  );
}

export function Section({ title, children, actions }: { title: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  );
}
