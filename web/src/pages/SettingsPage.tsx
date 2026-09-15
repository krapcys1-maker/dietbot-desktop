import { api } from "../api";
import { ErrorBox, Loading, useToast } from "../components/ui";
import type { Costs, Status } from "../types";
import { MEAL_NAMES, num, plural, usd, useAsync } from "../util";

const TASK_NAMES: Record<string, string> = {
  uklad_tygodnia: "układanie tygodnia", naprawa_planu: "naprawa planu", czat: "czat", czat_zamiana: "czat — wybór zamiany",
  generowanie_dan: "generowanie dań", poprawa_dan: "poprawianie dań", rozstrzyganie_produktow: "rozpoznawanie produktów",
};

export default function SettingsPage({ status }: { status: Status | null }) {
  const toast = useToast();
  const costs = useAsync<Costs>(() => api.costs(), []);

  if (!status) return <div className="page"><Loading /></div>;
  const pct = Math.min(100, (status.llm.spent_usd / Math.max(status.llm.budget_usd, 0.0001)) * 100);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Ustawienia i stan</h1>
          <div className="sub">Wszystkie dane są lokalnie w folderze aplikacji: dane/, konfiguracja/, wyniki/.</div>
        </div>
        <button className="btn" onClick={() => api.openResults().then((r) => toast(`Folder: ${r.path}`)).catch((e) => toast(e.message, "error"))}>
          📂 Otwórz folder wyników
        </button>
      </div>

      <section className="card stack">
        <h2>Model językowy</h2>
        <div className="row" style={{ gap: 24 }}>
          <div><span className="muted">Dostawca:</span> <b>{status.llm.provider}</b></div>
          <div><span className="muted">Model:</span> <b>{status.llm.model}</b></div>
          <div>
            <span className="muted">Klucz API:</span>{" "}
            {status.llm.key_present ? <span className="badge ok">ustawiony</span> : <span className="badge error">brak</span>}
          </div>
        </div>
        <div className="meter" style={{ gridTemplateColumns: "120px 1fr 220px" }}>
          <span className="muted">Budżet</span>
          <div className="meter-track"><div className="meter-band" style={{ left: 0, width: `${pct}%`, borderRight: "none" }} /></div>
          <span className="meter-val">{usd(status.llm.spent_usd)} z {usd(status.llm.budget_usd)}</span>
        </div>
        <div className="muted small">
          Klucz trzymamy wyłącznie w pliku <code>.env</code> (DEEPSEEK_API_KEY) — aplikacja go nie wyświetla. Model, ceny i budżet: <code>konfiguracja/llm.yaml</code>.
          Gdy budżet się skończy, plan układa algorytm, a czat jest wyłączony.
        </div>
        {costs.error && <ErrorBox error={costs.error} />}
        {costs.data && costs.data.by_task.length > 0 && (
          <table className="table">
            <thead><tr><th>Zadanie</th><th className="right">Wywołania</th><th className="right">Nieudane</th><th className="right">Koszt</th></tr></thead>
            <tbody>
              {costs.data.by_task.map((t) => (
                <tr key={t.task}>
                  <td>{TASK_NAMES[t.task] ?? t.task}</td>
                  <td className="right">{t.calls}</td>
                  <td className="right">{t.failed}</td>
                  <td className="right">{usd(t.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card stack">
        <h2>Dane</h2>
        <div className="row" style={{ gap: 24 }}>
          <div><span className="muted">Produkty:</span> <b>{status.products}</b></div>
          <div><span className="muted">Dania:</span> <b>{status.dishes}</b> (zatwierdzone {status.approved_dishes})</div>
          <div><span className="muted">Produkty z półek sklepów:</span> <b>{status.store_products}</b></div>
        </div>
        <div className="row" style={{ gap: 24 }}>
          {Object.entries(status.pools).map(([meal, n]) => (
            <div key={meal}><span className="muted">Pula — {MEAL_NAMES[meal]?.toLowerCase() ?? meal}:</span> <b>{n}</b></div>
          ))}
          {Object.values(status.batch_pools).map((n, i) => (
            <div key={i}><span className="muted">Dania na zapas:</span> <b>{n}</b></div>
          ))}
        </div>
        {status.data_errors.length > 0 && <div className="alert error">Błędy w danych:<ul>{status.data_errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
        {status.data_warnings.length > 0 && (
          <details>
            <summary>Uwagi do danych ({status.data_warnings.length})</summary>
            <ul className="small">{status.data_warnings.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </details>
        )}
        {status.data_errors.length === 0 && <div className="alert ok small">Dane są spójne.</div>}
      </section>

      <section className="card stack">
        <h2>Telegram</h2>
        {status.telegram.configured ? (
          <div className="alert ok">
            Bot jest skonfigurowany ({status.telegram.allowed_users} {plural(status.telegram.allowed_users, "dozwolony użytkownik", "dozwolonych użytkowników", "dozwolonych użytkowników")}).
            Uruchamia się razem z aplikacją.
          </div>
        ) : (
          <div className="alert info">
            Bot nie jest skonfigurowany. Aby go włączyć:
            <ol style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              <li>W Telegramie napisz do <b>@BotFather</b> komendę <code>/newbot</code> i skopiuj token.</li>
              <li>W pliku <code>.env</code> dopisz <code>TELEGRAM_BOT_TOKEN=…</code> (nie wklejaj tokenu nigdzie indziej).</li>
              <li>Napisz do swojego bota <code>/start</code> — odpowie Twoim numerem ID.</li>
              <li>Dopisz w <code>.env</code>: <code>TELEGRAM_ALLOWED_USERS=Twoje_ID,ID_żony</code> i uruchom aplikację ponownie.</li>
            </ol>
          </div>
        )}
        <div className="muted small">Komendy bota: /dzis, /jutro, /tydzien, /zakupy, /nowyplan, a zwykłe wiadomości trafiają do czatu.</div>
      </section>

      <section className="card small muted">
        Wartości odżywcze: BLS 4.0 (Max Rubner-Institut, CC BY 4.0), Open Food Facts (ODbL) i etykiety. Miary domowe: USDA FoodData Central,
        rozporządzenie UE 589/2008 i NHS. Produkty z półek: Monitorul Prețurilor (Consiliul Concurenței). Silnik: OR-Tools CP-SAT. Liczby: {num(status.products)} produktów w bazie.
      </section>
    </div>
  );
}
