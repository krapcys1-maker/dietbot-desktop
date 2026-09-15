import { useEffect, useState } from "react";
import { api } from "../api";
import { JobProgress, useToast } from "../components/ui";
import type { Job, Status } from "../types";
import { DAY_SHORT, go } from "../util";

const EXAMPLES = [
  "We wtorek i środę zupa pomidorowa",
  "W piątek schabowy z piersi kurczaka z ziemniakami i surówką",
  "W sobotę lody albo popcorn do filmu",
  "W piątek coś na szybko, w weekend więcej czasu na gotowanie",
  "Więcej ryb, mniej jajek",
  "Kuchnia włoska 2 razy, zupa, resztę wymyśl",
];

export default function GeneratePage({ status }: { status: Status | null }) {
  const toast = useToast();
  const [weekStart, setWeekStart] = useState<string>("");
  const [days, setDays] = useState<number[]>([0, 1, 2, 3, 4, 5, 6]);
  const [wishes, setWishes] = useState("");
  const [avoidRecent, setAvoidRecent] = useState(true);
  const [useLlm, setUseLlm] = useState(true);
  const [invent, setInvent] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [finished, setFinished] = useState<Job | null>(null);

  useEffect(() => {
    if (status && !weekStart) setWeekStart(status.default_week_start);
  }, [status, weekStart]);

  const llmAvailable = !!status?.llm.key_present && !!status?.llm.enabled;

  const toggleDay = (d: number) => setDays((ds) => (ds.includes(d) ? ds.filter((x) => x !== d) : [...ds, d].sort()));

  const start = async () => {
    if (days.length === 0) {
      toast("Wybierz przynajmniej jeden dzień.", "error");
      return;
    }
    if (weekStart && new Date(`${weekStart}T00:00:00`).getDay() !== 1) {
      toast("Początek tygodnia musi być poniedziałkiem — dni planu liczą się od poniedziałku.", "error");
      return;
    }
    try {
      setFinished(null);
      const j = await api.generate({ wishes: wishes.trim() || undefined, days, avoid_recent: avoidRecent, use_llm: useLlm && llmAvailable, week_start: weekStart || undefined, invent: invent && useLlm && llmAvailable });
      setJob(j);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Nowy plan tygodnia</h1>
          <div className="sub">Model językowy układa dania, silnik liczy gramatury, a niezależny weryfikator sprawdza każdą liczbę.</div>
        </div>
      </div>

      <section className="card stack">
        <div className="row" style={{ alignItems: "flex-end", gap: 18 }}>
          <label className="field">
            <span>Tydzień od (poniedziałek)</span>
            <input type="date" value={weekStart} onChange={(e) => setWeekStart(e.target.value)} />
          </label>
          <div className="field">
            <span>Dni</span>
            <div className="row">
              {DAY_SHORT.map((_, i) => (
                <label key={i} className="check">
                  <input type="checkbox" checked={days.includes(i)} onChange={() => toggleDay(i)} />
                  {DAY_SHORT[i]}
                </label>
              ))}
            </div>
          </div>
        </div>
        <label className="field">
          <span>Życzenia na ten tydzień (opcjonalnie)</span>
          <textarea rows={3} maxLength={1000} placeholder="np. w 2 dni zupa pomidorowa, w piątek schabowy z kurczaka z ziemniakami i surówką, w sobotę pizza — konkretne dania program znajdzie w bibliotece albo wymyśli" value={wishes} onChange={(e) => setWishes(e.target.value)} />
        </label>
        <div className="row small">
          {EXAMPLES.map((ex) => (
            <button key={ex} className="btn small ghost" onClick={() => setWishes((w) => (w ? `${w}; ${ex.toLowerCase()}` : ex))}>
              + {ex}
            </button>
          ))}
        </div>
        <div className="row" style={{ gap: 22 }}>
          <label className="check">
            <input type="checkbox" checked={avoidRecent} onChange={(e) => setAvoidRecent(e.target.checked)} />
            Inne dania niż w poprzednim planie
          </label>
          <label className="check" title="Model tworzy nowe przepisy z produktów z bazy, program je sprawdza i dodaje do biblioteki">
            <input type="checkbox" checked={invent && llmAvailable} disabled={!llmAvailable || !useLlm} onChange={(e) => setInvent(e.target.checked)} />
            Wymyśl nowe dania do tego tygodnia
          </label>
          <label className="check" title={llmAvailable ? "" : "Brak klucza API — tydzień ułoży algorytm"}>
            <input type="checkbox" checked={useLlm && llmAvailable} disabled={!llmAvailable} onChange={(e) => setUseLlm(e.target.checked)} />
            Układ przez model językowy {status ? `(${status.llm.model})` : ""}
          </label>
        </div>
        {!llmAvailable && status && <div className="alert info">Model językowy jest niedostępny — tydzień ułoży algorytm (bez życzeń słownych). Klucz API ustawia się w pliku .env.</div>}
        <div className="row">
          <button className="btn primary" onClick={start} disabled={!!job && !finished}>
            ✨ Ułóż plan
          </button>
          <span className="muted small">{invent ? "Z wymyślaniem nowych dań: zwykle 2–4 minuty." : "Zwykle 20–60 sekund."}</span>
        </div>
      </section>

      {job && (
        <section className="card stack">
          <h2>Postęp</h2>
          <JobProgress jobId={job.id} onDone={(j) => setFinished(j)} />
          {finished?.status === "gotowe" && !!finished.result?.new_dishes?.length && (
            <div className="alert ok small">
              Nowe dania w bibliotece: {finished.result.new_dishes.length}. Znajdziesz je w zakładce Dania (oznaczenie „wymyślone”) — możesz je tam odrzucić.
            </div>
          )}
          {finished?.status === "gotowe" && finished.result?.plan_id && (
            <div className="row">
              <button className="btn primary" onClick={() => go("tydzien", finished.result!.plan_id!)}>
                Zobacz plan →
              </button>
              <button className="btn" onClick={() => go("zakupy", finished.result!.plan_id!)}>
                🛒 Lista zakupów
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
