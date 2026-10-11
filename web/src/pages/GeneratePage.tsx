import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import PlateArt from "../components/PlateArt";
import Icon from "../components/Icon";
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
  const [submitting, setSubmitting] = useState(false);
  const initializedDate = useRef(false);
  const pending = useRef(false);
  const progressRef = useRef<HTMLElement>(null);

  // postęp pojawia się pod formularzem — przewijamy do niego, żeby było widać, że plan się układa
  useEffect(() => {
    if (job) progressRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [job?.id]);

  useEffect(() => {
    if (status && !initializedDate.current) {
      initializedDate.current = true;
      setWeekStart(status.default_week_start);
    }
  }, [status]);

  const llmAvailable = !!status?.llm.key_present && !!status?.llm.enabled;

  const toggleDay = (d: number) => setDays((ds) => (ds.includes(d) ? ds.filter((x) => x !== d) : [...ds, d].sort()));

  const start = async () => {
    if (pending.current || (job && !finished)) return;
    if (days.length === 0) {
      toast("Wybierz przynajmniej jeden dzień.", "error");
      return;
    }
    if (weekStart && new Date(`${weekStart}T00:00:00`).getDay() !== 1) {
      toast("Początek tygodnia musi być poniedziałkiem — dni planu liczą się od poniedziałku.", "error");
      return;
    }
    pending.current = true;
    setSubmitting(true);
    try {
      setFinished(null);
      const j = await api.generate({ wishes: useLlm && llmAvailable ? wishes.trim() || undefined : undefined, days, avoid_recent: avoidRecent, use_llm: useLlm && llmAvailable, week_start: weekStart || undefined, invent: invent && useLlm && llmAvailable });
      setJob(j);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      pending.current = false;
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">ZAPLANUJ COŚ DOBREGO</span>
          <h1>Co jemy w tym tygodniu?</h1>
          <div className="sub">Wybierz tydzień i opisz, na co masz ochotę. Porcje dopasujemy do zapisanych celów.</div>
        </div>
      </div>

      <div className="generation-layout">
      <section className="card stack generation-form">
        <h2><span className="section-number">1</span>Wybierz tydzień</h2>
        <div className="row" style={{ alignItems: "flex-end", gap: 18 }}>
          <label className="field">
            <span>Tydzień od (poniedziałek)</span>
            <input type="date" value={weekStart} onChange={(e) => setWeekStart(e.target.value)} />
          </label>
          <details className="disclosure">
            <summary>Dni planu ({days.length} z 7)</summary>
            <div className="row">
              {DAY_SHORT.map((_, i) => (
                <label key={i} className="check">
                  <input type="checkbox" checked={days.includes(i)} onChange={() => toggleDay(i)} />
                  {DAY_SHORT[i]}
                </label>
              ))}
            </div>
          </details>
        </div>
        <h2><span className="section-number">2</span>Dodaj coś od siebie</h2>
        <label className="field">
          <span>Życzenia na ten tydzień (opcjonalnie)</span>
          <textarea disabled={!useLlm || !llmAvailable} rows={3} maxLength={1000} placeholder="Np. w piątek coś szybkiego, w weekend kuchnia włoska." value={wishes} onChange={(e) => setWishes(e.target.value)} />
        </label>
        <details className="disclosure"><summary>Przykładowe życzenia</summary><div className="row small">
          {EXAMPLES.map((ex) => (
            <button key={ex} disabled={!useLlm || !llmAvailable} className="btn small ghost" onClick={() => setWishes((w) => (w ? `${w}; ${ex.toLowerCase()}` : ex))}>
              + {ex}
            </button>
          ))}
        </div></details>
        <details className="disclosure"><summary>Opcje układania</summary><div className="stack">
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
            Pomoc AI w wyborze dań {status ? `(${status.llm.model})` : ""}
          </label>
        </div></details>
        {status && !llmAvailable && <div className="alert info">Plan ułoży algorytm z biblioteki dań — życzenia słowne i wymyślanie dań wymagają AI. {status.llm.key_present
          ? <>AI jest wyłączone w tej sesji aplikacji — szczegóły w <a href="#/ustawienia">Ustawieniach</a>.</>
          : <>Aby z nich korzystać, dodaj własny klucz DeepSeek w <a href="#/ustawienia">Ustawieniach</a>.</>}</div>}
        {status && llmAvailable && !useLlm && <div className="alert info">Plan ułoży algorytm — bez życzeń słownych. Pomoc AI włączysz w opcjach układania.</div>}
        <div className="row">
          <button className="btn primary" onClick={start} disabled={!status || submitting || (!!job && !finished)}>
            <Icon name="sparkles" />{submitting ? "Uruchamianie…" : "Ułóż mój plan"}
          </button>
          <span className="muted small">{invent ? "Z wymyślaniem nowych dań: zwykle 2–4 minuty." : "Zwykle 20–60 sekund."}</span>
        </div>
      </section>
      <aside className="generation-aside"><PlateArt /><h2>Jeden plan.<br />Mniej codziennych decyzji.</h2><p>Dania i gramatury dobieramy według zapisanych celów. Gotowy tydzień możesz zmieniać, oceniać i zapisać razem z listą zakupów.</p><a href="#/zalozenia" className="text-link">Sprawdź cele i rodzaj diety <Icon name="arrow" /></a></aside>
      </div>

      {job && (
        <section className="card stack" ref={progressRef}>
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
                <Icon name="basket" />Lista zakupów
              </button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
