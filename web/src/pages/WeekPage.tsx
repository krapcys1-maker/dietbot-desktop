import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import DishRating from "../components/DishRating";
import SaveDietButton from "../components/SaveDietButton";
import ExportButtons from "../components/ExportButtons";
import MoreActions from "../components/MoreActions";
import Icon from "../components/Icon";
import { ErrorBox, JobProgress, Loading, Modal, Spinner, StateBadge, TagBadges, useToast } from "../components/ui";
import type { Day, DayBreakdown, Job, Meal, Metric, Person, PlanView, SwapOptions } from "../types";
import { DAY_SHORT, dateTime, dayDate, go, loadLocal, num, plural, saveLocal, todayIndex, usd, useAsync } from "../util";

const DAYS_ACC: Record<string, string> = {
  poniedziałek: "poniedziałek", wtorek: "wtorek", środa: "środę", czwartek: "czwartek", piątek: "piątek", sobota: "sobotę", niedziela: "niedzielę",
};

const METRICS: { key: Metric; label: string; unit: string }[] = [
  { key: "kcal", label: "Energia", unit: "kcal" },
  { key: "protein", label: "Białko", unit: "g" },
  { key: "fat", label: "Tłuszcz", unit: "g" },
  { key: "carbs", label: "Węglowodany", unit: "g" },
];

const SALT_LIMIT_G = 5; // WHO: mniej niż 5 g soli dziennie
const SAT_FAT_LIMIT_PCT = 10;

/** Błonnik, sól i kwasy nasycone dnia — do oceny jakości diety, nie do celów silnika (audyt 09.10.2026, S9). */
function QualityLine({ values }: { values: { fiber?: number; salt?: number; saturated_fat_pct?: number } }) {
  const salt = values.salt ?? 0;
  const sat = values.saturated_fat_pct ?? 0;
  return (
    <div className="muted small">
      Błonnik {num(values.fiber ?? 0)} g ·{" "}
      <span className={salt > SALT_LIMIT_G ? "warn-text" : undefined}
        title="Sól z produktów i soli kuchennej w przepisach. WHO zaleca mniej niż 5 g dziennie.">
        sól {num(salt, 1)} g{salt > SALT_LIMIT_G ? " (WHO: < 5 g)" : ""}
      </span>{" "}·{" "}
      <span className={sat > SAT_FAT_LIMIT_PCT ? "warn-text" : undefined}
        title="Kwasy tłuszczowe nasycone jako procent energii dnia. Zalecenie: mniej niż 10%.">
        kwasy nasycone {num(sat, 1)}% energii{sat > SAT_FAT_LIMIT_PCT ? " (zalecane < 10%)" : ""}
      </span>
    </div>
  );
}

export default function WeekPage({ planId }: { planId?: string }) {
  const toast = useToast();
  const { data: plan, error, loading, reload } = useAsync<PlanView | null>(
    () => (planId ? api.plan(planId) : api.activePlan().catch((e) => (e.status === 404 ? null : Promise.reject(e)))),
    [planId],
  );
  const [day, setDay] = useState<number>(0);
  const [swapMeal, setSwapMeal] = useState<Meal | null>(null);
  const [job, setJob] = useState<{ id: string; title: string } | null>(null);
  const [jobRunning, setJobRunning] = useState(false);
  const [starting, setStarting] = useState(false);
  const [showLog, setShowLog] = useState(false);

  useEffect(() => {
    if (!plan) return;
    const today = todayIndex(plan.week_start);
    const saved = loadLocal<number | null>(`dzien:${plan.family_id}`, null);
    // po zamianie albo zmianie wersji wracamy do oglądanego dnia; „dziś” tylko przy pierwszym otwarciu planu
    const initial = saved ?? today ?? plan.days[0]?.day ?? 0;
    setDay(plan.days.some((d) => d.day === initial) ? initial : plan.days[0]?.day ?? 0);
  }, [plan?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading && !plan) return <div className="page"><Loading /></div>;
  if (error) return <div className="page"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!plan)
    return (
      <div className="page">
        <div className="card empty">
          <Icon name="calendar" />
          <h2>Nie ma jeszcze planu</h2>
          <p>Ułóż pierwszy tydzień — program dobierze dania i policzy gramatury dla obu osób.</p>
          <button className="btn primary" onClick={() => go("nowy")}>Ułóż plan tygodnia</button>
        </div>
      </div>
    );

  const current = plan.days.find((d) => d.day === day) ?? plan.days[0];
  const errors = plan.issues.filter((i) => i.severity === "blad");
  const warnings = plan.issues.filter((i) => i.severity === "ostrzezenie");
  const today = todayIndex(plan.week_start);

  const startJob = async (kind: "recompute" | "repair") => {
    if (jobRunning || starting) return; // podwójne kliknięcie albo Enter tworzyły dwie wersje (audyt S1)
    setStarting(true);
    try {
      const j = await api.recompute(plan.id, kind === "repair");
      setJobRunning(true);
      setJob({ id: j.id, title: kind === "repair" ? "Przeliczenie z naprawą" : "Przeliczenie planu" });
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setStarting(false);
    }
  };

  const onJobDone = (j: Job) => {
    setJobRunning(false);
    if (j.status === "gotowe" && j.result?.plan_id) {
      toast(j.result.ok ? "Nowa wersja planu jest gotowa." : "Nowa wersja ma uwagi — sprawdź podsumowanie.");
      if (j.result.rule_warnings?.length) toast("Zasady tygodnia: " + j.result.rule_warnings.join("; "), "error");
      setTimeout(() => {
        setJob(null);
        go("tydzien", j.result!.plan_id!);
      }, 700);
    }
  };

  const remove = async () => {
    if (!window.confirm(`Usunąć „${plan.title}” (wersja ${plan.version})?`)) return;
    try {
      await api.deletePlan(plan.id);
      toast("Plan usunięty.");
      // bez id w adresie zmiana trasy nic nie odświeży — trzeba wczytać aktualny plan ponownie
      if (planId) go("tydzien");
      else reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">TWÓJ TYDZIEŃ NA TALERZU</span>
          <div className="row">
            <h1>{plan.title}</h1>
            {plan.ok ? <span className="badge ok"><Icon name="check" />zgodny z założeniami</span> : errors.length ? <span className="badge error">naruszenia założeń</span> : <span className="badge warn">uwagi</span>}
            {plan.active && <span className="badge">aktualny</span>}
            {plan.saved_diet && <span className="badge ok">💾 {plan.saved_diet.title}</span>}
          </div>
          <div className="sub">{plan.days.length} {plural(plan.days.length, "dzień", "dni", "dni")} · {plan.persons.map((p) => p.name).join(" i ")} · wersja {plan.version}</div>
        </div>
      </div>
      <div className="plan-toolbar no-print">
        <ExportButtons key={plan.id} planId={plan.id} />
        <div className="row secondary-actions">
          <SaveDietButton key={plan.id} plan={plan} onSaved={reload} />
          <MoreActions>
          <button className="btn" onClick={() => go("zakupy", plan.id)}>Otwórz listę zakupów</button>
          {plan.versions.length > 1 && (
            <select value={plan.id} onChange={(e) => go("tydzien", e.target.value)} title="Wersje planu" style={{ maxWidth: 300 }}>
              {plan.versions.map((v) => (
                <option key={v.id} value={v.id}>
                  Wersja {v.version}{v.change_note ? ` — ${v.change_note}` : ""}
                </option>
              ))}
            </select>
          )}
          {!plan.active && (
            <button className="btn" onClick={() => api.patchPlan(plan.id, { active: true }).then(() => { toast("Ustawiono jako aktualny plan."); reload(); }).catch((e) => toast((e as Error).message, "error"))}>
              Ustaw jako aktualny
            </button>
          )}
          <button className="btn danger" onClick={remove}>Usuń</button>
          </MoreActions>
        </div>
      </div>

      {plan.assumptions_changed && (
        <div className="alert warn row no-print">
          <span className="grow">Cele, preferencje z notatnika, przepisy lub zasady proporcji zmieniły się od zapisu. Ta dieta zachowuje zapisane dane, liczby i zakupy.
            Przelicz, aby utworzyć nową wersję według aktualnych ustawień.</span>
          <button className="btn small" disabled={jobRunning || starting} onClick={() => startJob("recompute")}>Przelicz te same dania</button>
          <button className="btn small" disabled={jobRunning || starting} onClick={() => startJob("repair")}>Przelicz i napraw</button>
        </div>
      )}
      {!plan.ok && !plan.assumptions_changed && (
        <div className="alert warn row no-print">
          <span className="grow">Plan ma uwagi weryfikatora (szczegóły na dole). Możesz zamienić danie albo spróbować automatycznej naprawy.</span>
          <button className="btn small" disabled={jobRunning || starting} onClick={() => startJob("repair")}>Napraw automatycznie</button>
        </div>
      )}

      <div className="plan-overviews">
      {(plan.comment || plan.facts.length > 0 || plan.snapshot_legacy) && (
        <details className="card disclosure">
          <summary>O tym planie <span className="summary-hint">Opis, zasady i szczegóły</span></summary>
          <div className="stack">
          {plan.snapshot_legacy && <div className="muted small">Starszy zapis: dane zabezpieczono {plan.snapshot_date ? new Date(plan.snapshot_date).toLocaleDateString("pl-PL") : "przy aktualizacji"}.
            Aplikacja nie miała wcześniejszej kopii tabel i założeń; nie jest to odtworzenie ich historycznych wartości.</div>}
          <div className="muted small">{plan.source_label} · {dateTime(plan.created_at)}
            {plan.llm_cost_usd > 0 ? ` · koszt AI ${usd(plan.llm_cost_usd)}` : ""}
            {plan.change_note ? ` · ${plan.change_note}` : ""}</div>
          {plan.wishes && <div className="small">Życzenia: {plan.wishes}</div>}
          {plan.comment && <blockquote className="plan-comment">{plan.comment}</blockquote>}
          <div className="facts">
            {plan.facts.map((f, i) => (
              <div key={i} className={`fact ${f.ok ? "" : "bad"}`}>
                <div>
                  {f.label}: <b>{f.value}</b>
                </div>
                {f.detail && <div className="detail">{f.detail}</div>}
              </div>
            ))}
          </div>
          </div>
        </details>
      )}

      <details className="card disclosure week-overview">
        <summary>Cały tydzień w skrócie <span className="summary-hint">Wszystkie dania na jednym ekranie</span></summary>
        <div className="week-grid">
          {plan.days.map((d) => <button className={`week-day ${d.day === current?.day ? "selected" : ""}`} key={d.day}
            onClick={(e) => { setDay(d.day); saveLocal(`dzien:${plan.family_id}`, d.day); e.currentTarget.closest("details")?.removeAttribute("open"); }}>
            <b>{d.label} {plan.week_start && <span>{dayDate(plan.week_start, d.day)}</span>}</b>
            {d.meals.map((m) => <span className="week-meal" key={m.slot_key}><small>{m.meal_label}</small>{m.dish_name}</span>)}
            {!!d.snacks.length && <small>+ przekąski: {[...new Set(d.snacks.map((s) => s.dish_name))].join(", ")}</small>}
          </button>)}
        </div>
      </details>

      </div>
      <div className="tabs no-print">
        {plan.days.map((d) => {
          const bad = d.issues.some((i) => i.severity === "blad") || Object.values(d.totals).some((t) => Object.values(t.states).some((s) => s !== "ok"));
          return (
            <button key={d.day} aria-pressed={d.day === current?.day} className={`tab ${d.day === current?.day ? "active" : ""}`} onClick={() => { setDay(d.day); saveLocal(`dzien:${plan.family_id}`, d.day); }}>
              <span className={`dot ${bad ? "bad" : ""}`} />
              {DAY_SHORT[d.day]}
              {plan.week_start && <span className="today">{dayDate(plan.week_start, d.day)}</span>}
              {today === d.day && <span className="today">· dziś</span>}
            </button>
          );
        })}
      </div>

      {current && <DayView key={`${plan.id}:${current.day}`} day={current} plan={plan} onSwap={setSwapMeal} />}
      {!!plan.week_summary?.length && <details className="disclosure"><summary>Bilans całego tygodnia</summary><WeekSummaryCard plan={plan} /></details>}

      {(errors.length > 0 || warnings.length > 0) && (
        <section className="card stack">
          <h2>Uwagi weryfikatora</h2>
          {errors.length > 0 && (
            <div className="alert error">
              <ul>{errors.map((i, k) => <li key={k}>{i.message}</li>)}</ul>
            </div>
          )}
          {warnings.length > 0 && (
            <div className="alert warn">
              <ul>{warnings.map((i, k) => <li key={k}>{i.message}</li>)}</ul>
            </div>
          )}
        </section>
      )}

      <section className="card no-print">
        <button className="btn ghost" onClick={() => setShowLog((s) => !s)}>
          {showLog ? "▾" : "▸"} Jak powstał ten plan
        </button>
        {showLog && (
          <div className="stack" style={{ marginTop: 10 }}>
            <div className="joblog">{plan.log.join("\n") || "Brak dziennika."}</div>
            <div className="muted small">Silnik: {plan.engine} · czas liczenia {num(plan.seconds, 1)} s</div>
          </div>
        )}
      </section>

      {swapMeal && (
        <SwapDialog
          plan={plan}
          meal={swapMeal}
          onClose={() => setSwapMeal(null)}
          onStarted={(j) => {
            setSwapMeal(null);
            setJobRunning(true);
            setJob({ id: j.id, title: j.title });
          }}
        />
      )}
      {job && (
        <Modal title={job.title} onClose={() => {
          if (!jobRunning) { setJob(null); return; }
          // ✕ w trakcie liczenia: pytamy o przerwanie (wcześniej przycisk nic nie robił — audyt S13)
          if (window.confirm("Przerwać przeliczanie? Nowa wersja planu nie powstanie.")) api.cancelJob(job.id).catch(() => undefined);
        }}>
          <JobProgress jobId={job.id} onDone={onJobDone} />
          {jobRunning && <div className="muted small">Okno zamknie się po zakończeniu — nowa wersja planu otworzy się sama.</div>}
        </Modal>
      )}
    </div>
  );
}

function DayView({ day, plan, onSwap }: { day: Day; plan: PlanView; onSwap: (m: Meal) => void }) {
  return (
    <div className="stack">
      <div className="day-heading"><h2>{day.label}</h2><span className="muted small">Łącznie z przekąskami i stałymi pozycjami</span></div>
      <div className="daily-summary">
        {plan.persons.map((p) => { const total = day.totals[p.id]; if (!total) return null;
          const ok = Object.values(total.states).every((s) => s === "ok");
          return <section className="card flat" key={p.id}>
            <div className="card-head"><b>{p.name}</b><span className={`badge ${ok ? "ok" : "warn"}`}>{ok ? "W celach" : "Sprawdź cele"}</span></div>
            <div className="macro-grid">{METRICS.map((m) => <div key={m.key} className={total.states[m.key] !== "ok" ? "bad-text" : ""}>
              <span>{m.label}</span><b>{num(total.values[m.key])}<small> {m.unit}</small></b>
              <small>cel {num(p.targets[m.key].min)}–{num(p.targets[m.key].max)}</small>
            </div>)}</div>
            <QualityLine values={total.values} />
          </section>;
        })}
      </div>
      {day.meals.map((m) => (
        <MealCard key={m.slot_key + m.meal} meal={m} persons={plan.persons} onSwap={() => onSwap(m)} />
      ))}
      {day.snacks.length > 0 && (
        <section className="card">
          <h3 style={{ marginBottom: 6 }}>Przekąski bilansujące</h3>
          {day.snacks.map((s, i) => {
            const person = plan.persons.find((p) => p.id === s.person_id);
            return (
              <div key={i} className="snack">
                <b className="nowrap">{person?.name}:</b>
                <div className="grow">
                  {s.items.map((it) => `${it.name.toLowerCase()} ${it.amount}${it.household ? ` (≈ ${it.household})` : ""}`).join(", ")}
                  <div className="muted small">{s.reason}</div>
                  <DishRating id={s.dish_id} name={s.dish_name} />
                </div>
                <span className="muted nowrap">{num(s.nutrition.kcal)} kcal · B {num(s.nutrition.protein)} g</span>
              </div>
            );
          })}
        </section>
      )}
      <details className="card disclosure">
        <summary>Dokładne rozliczenie dnia <span className="summary-hint">Skąd pochodzą kcal i makro</span></summary>
        <div className="card-head">
          <h3>Suma dnia — {day.label}</h3>
          <span className="muted small">
            {plan.fixed_items.length > 0 ? `ze stałymi pozycjami (${plan.fixed_items.map((f) => f.description || f.name).join(", ")}) · ` : ""}liczy weryfikator
          </span>
        </div>
        {day.breakdown && Object.keys(day.breakdown).length > 0 && (
          <div className="sum-grid" style={{ marginBottom: 14 }}>
            {plan.persons.map((p) => day.breakdown?.[p.id] && (
              <DayBreakdownTable key={p.id} person={p} data={day.breakdown[p.id]} />
            ))}
          </div>
        )}
        <div className="totals-grid">
          {plan.persons.map((p) => (
            <PersonTotals key={p.id} person={p} totals={day.totals[p.id]} />
          ))}
        </div>
      </details>
    </div>
  );
}

const STATE_LABEL: Record<string, string> = { ok: "", za_malo: "za mało", za_duzo: "za dużo" };

function cellClass(state?: string) {
  return state && state !== "ok" ? "num bad-cell" : "num";
}

function DayBreakdownTable({ person, data }: { person: Person; data: DayBreakdown }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <b>{person.name}</b>
      <div className="table-scroll">
        <table className="table sum-table">
          <thead>
            <tr>
              <th>Co</th>
              {METRICS.map((m) => <th key={m.key} className="num">{m.key === "kcal" ? "kcal" : `${m.label[0]} (g)`}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r, i) => (
              <tr key={i} className={`row-${r.kind}`}>
                <td>{r.label}</td>
                {METRICS.map((m) => <td key={m.key} className="num">{num(r.values[m.key])}</td>)}
              </tr>
            ))}
            <tr className="sum-row">
              <td>Razem dzień</td>
              {METRICS.map((m) => (
                <td key={m.key} className={cellClass(data.states[m.key])} title={STATE_LABEL[data.states[m.key]]}>
                  {num(data.total[m.key])}
                </td>
              ))}
            </tr>
            <tr className="band-row">
              <td>Widełki</td>
              {METRICS.map((m) => <td key={m.key} className="num">{num(person.targets[m.key].min)}–{num(person.targets[m.key].max)}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
      <div className={`small ${data.consistent ? "muted" : "bad-text"}`}>
        {data.consistent
          ? `${Object.values(data.states).every((s) => s === "ok") ? "✓ w widełkach" : "⚠ poza widełkami"} · suma wierszy zgadza się z weryfikatorem`
          : "⚠ suma wierszy nie zgadza się z weryfikatorem — zgłoś to"}
      </div>
    </div>
  );
}

function WeekSummaryCard({ plan }: { plan: PlanView }) {
  return (
    <section className="card">
      <div className="card-head">
        <h3>Podsumowanie tygodnia</h3>
        <span className="muted small">sumy dni z weryfikatora · średnia porównana z widełkami dziennymi</span>
      </div>
      <div className="sum-grid">
        {(plan.week_summary ?? []).map((w) => {
          const person = plan.persons.find((p) => p.id === w.person_id);
          if (!person) return null;
          const allOk = w.days_ok === w.days_count && Object.values(w.average_states).every((s) => s === "ok");
          return (
            <div key={w.person_id} className="stack" style={{ gap: 6 }}>
              <b>{person.name}</b>
              <div className="table-scroll">
                <table className="table sum-table">
                  <thead>
                    <tr>
                      <th>Dzień</th>
                      {METRICS.map((m) => <th key={m.key} className="num">{m.key === "kcal" ? "kcal" : `${m.label[0]} (g)`}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {w.days.map((d) => (
                      <tr key={d.day}>
                        <td>{d.label}</td>
                        {METRICS.map((m) => <td key={m.key} className={cellClass(d.states[m.key])}>{num(d.values[m.key])}</td>)}
                      </tr>
                    ))}
                    <tr className="sum-row">
                      <td>Razem tydzień</td>
                      {METRICS.map((m) => <td key={m.key} className={cellClass(w.total_states[m.key])}>{num(w.total[m.key])}</td>)}
                    </tr>
                    <tr className="band-row">
                      <td>Widełki na {w.days_count} {w.days_count === 1 ? "dzień" : "dni"}</td>
                      {METRICS.map((m) => <td key={m.key} className="num">{num(w.week_band[m.key][0])}–{num(w.week_band[m.key][1])}</td>)}
                    </tr>
                    <tr className="sum-row">
                      <td>Średnio dziennie</td>
                      {METRICS.map((m) => <td key={m.key} className={cellClass(w.average_states[m.key])}>{num(w.average[m.key])}</td>)}
                    </tr>
                    <tr className="band-row">
                      <td>Widełki dzienne</td>
                      {METRICS.map((m) => <td key={m.key} className="num">{num(person.targets[m.key].min)}–{num(person.targets[m.key].max)}</td>)}
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className={`small ${allOk ? "muted" : "bad-text"}`}>
                {allOk ? "✓" : "⚠"} dni w widełkach: {w.days_ok} z {w.days_count}
                {" · "}średnia {Object.values(w.average_states).every((s) => s === "ok") ? "w widełkach" : "poza widełkami"}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PersonTotals({ person, totals }: { person: Person; totals?: Day["totals"][string] }) {
  if (!totals) return null;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <b>{person.name}</b>
      {METRICS.map((m) => {
        const band = person.targets[m.key];
        const value = totals.values[m.key];
        const lo = band.min * 0.8;
        const hi = band.max * 1.15;
        const pct = (v: number) => `${Math.min(100, Math.max(0, ((v - lo) / (hi - lo)) * 100))}%`;
        const ok = totals.states[m.key] === "ok";
        return (
          <div key={m.key} className="meter" title={`widełki ${num(band.min)}–${num(band.max)} ${m.unit}`}>
            <span className="muted">{m.label}</span>
            <div className="meter-track">
              <div className="meter-band" style={{ left: pct(band.min), width: `calc(${pct(band.max)} - ${pct(band.min)})` }} />
              <div className={`meter-mark ${ok ? "" : "bad"}`} style={{ left: pct(value) }} />
            </div>
            <span className="meter-val">
              <b>{num(value)}</b> {m.unit} <span className="muted small">/ {num(band.min)}–{num(band.max)}</span>
            </span>
          </div>
        );
      })}
      <QualityLine values={totals.values} />
    </div>
  );
}

function MealCard({ meal, persons, onSwap }: { meal: Meal; persons: Person[]; onSwap: () => void }) {
  const rows = useMemo(() => {
    const order: string[] = [];
    const names: Record<string, string> = {};
    for (const p of persons) {
      for (const it of meal.portions[p.id]?.items ?? []) {
        if (!order.includes(it.product_id)) order.push(it.product_id);
        names[it.product_id] = it.name;
      }
    }
    const groupOf: Record<string, string> = {};
    for (const g of meal.groups ?? []) for (const it of g.items) groupOf[it.product_id] = g.name;
    return order.map((pid) => ({ pid, name: names[pid], group: groupOf[pid] }));
  }, [meal, persons]);

  return (
    <section className="card meal-card">
      <div className="meal-top">
        <div>
          <div className="meal-label">{meal.meal_label}</div>
          <div className="meal-name">{meal.dish_name}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <TagBadges tags={meal.tags} isTreat={meal.is_treat} isSweet={meal.is_sweet} diets={meal.diets} />
            {meal.cuisine && <span className="badge">kuchnia {meal.cuisine}</span>}
            {meal.prep_minutes && <span className="badge"><Icon name="clock" />{meal.prep_minutes} min</span>}
            {meal.batch_group && <span className="badge batch">{meal.reheat ? "odgrzewasz" : "gotujesz"}: {meal.batch_days.join(", ").toLowerCase()}</span>}
          </div>
        </div>
        <button className="btn no-print" onClick={onSwap} aria-label={`Zamień ${meal.meal_label.toLowerCase()}: ${meal.dish_name}`}>↻ Zamień</button>
      </div>
      {meal.description && <p className="meal-desc">{meal.description}</p>}
      <div className="meal-summary">{persons.map((p) => { const por = meal.portions[p.id]; if (!por) return null;
        const n = por.nutrition;
        return <div key={p.id}><span>{p.name}</span><b>{num(n.kcal)} kcal</b><small>B {num(n.protein)} · T {num(n.fat)} · W {num(n.carbs)} g</small>
          {(por.mass_over || (por.anchor_state && por.anchor_state !== "ok")) && <small className="bad-text">Sprawdź szczegóły porcji</small>}</div>;
      })}</div>
      <DishRating id={meal.dish_id} name={meal.dish_name} />
      {meal.batch_recipe && !meal.reheat && <div className="pot stack">
        <h3>{meal.batch_recipe.title}</h3>
        <p>{meal.batch_recipe.instructions}</p>
        <table className="table"><thead><tr><th>Składnik</th><th>Łącznie na wszystkie dni i osoby</th></tr></thead>
          <tbody>{meal.batch_recipe.items.map(i => <tr key={i.product_id}><td>{i.name}{i.side && <div className="muted small">Dodatek do podania osobno</div>}</td><td>{i.amount}</td></tr>)}
          {meal.batch_recipe.water && <tr><td>Woda do gotowania</td><td>{meal.batch_recipe.water}</td></tr>}</tbody></table>
        {meal.steps.length > 0 && <><b>Przygotowanie całej ilości — wykonujesz raz</b><ol className="steps">{meal.steps.map((s,i)=><li key={i}>{s}</li>)}</ol></>}
      </div>}
      {meal.reheat && <div className="alert info">Dziś tylko odgrzewasz przygotowane porcje. Nie gotujesz drugi raz.
        Pełne składniki i przepis na całość znajdziesz przy dniu: {meal.batch_days[0]?.toLowerCase()}.{meal.batch_recipe?.has_side ? " Pieczywo podaj osobno." : ""}</div>}
      <details className="disclosure meal-ingredients">
        <summary>{meal.batch_group ? "Podział gotowej partii — porcje na dzisiaj" : "Składniki i gramatury"} <span className="summary-hint">{rows.length} {plural(rows.length, "składnik", "składniki", "składników")} · porcje dla {persons.length} {plural(persons.length, "osoby", "osób", "osób")}</span></summary>
      {meal.batch_group && <p className="muted">Te ilości surowych składników są już zawarte w tabeli na całość. To nie jest druga lista do gotowania. Mięso i większe kawałki rozdziel według porcji.</p>}
      <div style={{ overflowX: "auto" }}>
        <table className="portions">
          <thead>
            <tr>
              <th>Składnik</th>
              {persons.map((p) => (
                <th key={p.id} className="num">{p.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.pid} className={r.group ? "in-group" : undefined}>
                <td>
                  {r.name}
                  {r.group && <div className="hh group-tag">{r.group}</div>}
                </td>
                {persons.map((p) => {
                  const it = meal.portions[p.id]?.items.find((x) => x.product_id === r.pid);
                  return (
                    <td key={p.id} className="num">
                      {it ? (
                        <>
                          {it.amount}
                          {it.household && <div className="hh">≈ {it.household}</div>}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>Razem</td>
              {persons.map((p) => {
                const por = meal.portions[p.id];
                if (!por) return <td key={p.id} />;
                const n = por.nutrition;
                return (
                  <td key={p.id} className="num">
                    {num(n.kcal)} kcal
                    <div className={`hh ${por.anchor_state && por.anchor_state !== "ok" ? "anchor-bad" : ""}`}>
                      B {num(n.protein)} · T {num(n.fat)} · W {num(n.carbs)} g {por.anchor_state && por.anchor_state !== "ok" && <StateBadge state={por.anchor_state} />}
                    </div>
                    <div className={`hh ${por.mass_over ? "anchor-bad" : ""}`} title={por.mass_cap_g ? `limit talerza: ${num(por.mass_cap_g)} g` : undefined}>
                      porcja {num(por.mass_g)} g{por.mass_over ? " — duża" : ""}
                    </div>
                  </td>
                );
              })}
            </tr>
          </tfoot>
        </table>
      </div>
      {!meal.batch_group && (meal.groups ?? []).map((g) => (
        <div key={g.name} className="group-box">
          <b>{g.name.charAt(0).toUpperCase() + g.name.slice(1)}</b> — przygotuj razem ({num(g.total_g)} g, proporcje z przepisu):{" "}
          {g.items.map((i) => `${i.name.toLowerCase()} ${i.amount}${i.household ? ` (≈ ${i.household})` : ""}`).join(", ")}
        </div>
      ))}
      {!meal.batch_recipe && meal.pot && meal.pot.length > 0 && (
        <div className="pot">
          <b>Gotujesz raz, na wszystkie porcje:</b>{" "}
          {meal.pot.map((p) => `${p.name.toLowerCase()} ${p.amount}${p.household ? ` (≈ ${p.household})` : ""}`).join(", ")}
        </div>
      )}
      </details>
      {meal.batch_recipe ? null : meal.reheat ? (
        <div className="muted">Odgrzewasz porcję ugotowaną wcześniej.</div>
      ) : (
        meal.steps.length > 0 && (
          <details>
            <summary>Przygotowanie</summary>
            <ol className="steps">
              {meal.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ol>
          </details>
        )
      )}
    </section>
  );
}

function SwapDialog({ plan, meal, onClose, onStarted }: { plan: PlanView; meal: Meal; onClose: () => void; onStarted: (j: Job) => void }) {
  const toast = useToast();
  const { data, error, loading } = useAsync<SwapOptions>(() => api.swapOptions(plan.id, meal.slot_key), [plan.id, meal.slot_key]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const options = (data?.options ?? []).filter((o) => {
    const hay = `${o.name} ${o.description ?? ""} ${o.cuisine} ${o.profile?.protein_sources.join(" ") ?? ""}`.toLowerCase();
    return filter.toLowerCase().split(/\s+/).every((w) => hay.includes(w));
  });

  const submit = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      onStarted(await api.swap(plan.id, meal.slot_key, selected));
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  return (
    <Modal
      title={`Zamień: ${meal.meal_label.toLowerCase()} — ${meal.dish_name}`}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Anuluj</button>
          <button className="btn primary" disabled={!selected || busy} onClick={submit}>
            {busy ? <Spinner /> : "Zamień i przelicz"}
          </button>
        </>
      }
    >
      <div className="muted">
        Silnik przeliczy gramatury tylko na {data ? data.days.map((d) => DAYS_ACC[d.toLowerCase()] ?? d.toLowerCase()).join(" i ") : "ten dzień"} — reszta tygodnia zostaje bez zmian. Powstanie nowa wersja planu.
      </div>
      <input type="search" placeholder="Szukaj: kurczak, makaron, kuchnia włoska…" value={filter} onChange={(e) => setFilter(e.target.value)} autoFocus />
      {loading && <Loading label="Szukam pasujących dań…" />}
      {error && <ErrorBox error={error} />}
      {options.map((o) => (
        <button key={o.id} className={`option ${selected === o.id ? "selected" : ""}`} aria-pressed={selected === o.id} onClick={() => setSelected(o.id)}>
          <div className="row">
            <b className="grow">{o.name}</b>
            <TagBadges tags={o.tags} diets={o.diets} />
          </div>
          {o.description && <div className="muted small">{o.description}</div>}
          <div className="row small muted">
            <span>kuchnia {o.cuisine}</span>
            {o.profile && <span>białko: {o.profile.protein_sources.join(", ") || "—"} ({o.profile.protein_label})</span>}
            {o.profile && <span>{o.profile.energy_label}</span>}
            {o.prep_minutes && <span>⏱ {o.prep_minutes} min</span>}
          </div>
          {o.notes.length > 0 && <div className="small" style={{ color: "var(--warn)" }}>⚠ {o.notes.join("; ")}</div>}
        </button>
      ))}
      {data && options.length === 0 && <div className="muted">Brak dań pasujących do wyszukiwania.</div>}
    </Modal>
  );
}
