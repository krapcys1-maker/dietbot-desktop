import { useState } from "react";
import { api } from "../api";
import { ErrorBox, JobProgress, Loading, Modal, Spinner, TagBadges, useToast } from "../components/ui";
import type { DishDetail, DishListItem, Job } from "../types";
import { MEAL_NAMES, STATUS_NAMES, go, num, useAsync } from "../util";

const ROLE_NAMES: Record<string, string> = {
  bialko: "białko", weglowodany: "węglowodany", tluszcz: "tłuszcz", warzywa: "warzywa", owoce: "owoce", nabial: "nabiał",
  sos: "sos", dodatek: "dodatek", przyprawa: "przyprawa",
};
const PORTIONING: Record<string, string> = { osobno: "osobno na talerz", z_garnka: "z jednego garnka", po_rowno: "po równo" };

export default function DishesPage({ dishId }: { dishId?: string }) {
  const [meal, setMeal] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [inventing, setInventing] = useState(false);
  const list = useAsync<DishListItem[]>(() => api.dishes({ posilek: meal || undefined, status: status || undefined, q: q || undefined }), [meal, status, q]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Biblioteka dań</h1>
          <div className="sub">Z tych dań model układa tydzień. Dania odrzucone nie trafiają do planów.</div>
        </div>
        <button className="btn primary" onClick={() => setInventing(true)}>✨ Wymyśl nowe dania</button>
      </div>
      {inventing && <InventDialog onClose={() => { setInventing(false); list.reload(); }} />}
      <div className="row">
        <input type="search" placeholder="Szukaj po nazwie, składniku, kuchni…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 280 }} />
        <select value={meal} onChange={(e) => setMeal(e.target.value)}>
          <option value="">Wszystkie posiłki</option>
          {Object.entries(MEAL_NAMES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Każdy status</option>
          {Object.entries(STATUS_NAMES).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {list.data && <span className="muted">{list.data.length} dań</span>}
      </div>
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      <div className="split">
        <div className="list">
          {list.loading && !list.data && <Loading />}
          {list.data?.map((d) => (
            <button key={d.id} className={`list-item ${dishId === d.id ? "selected" : ""}`} onClick={() => go("dania", d.id)}>
              <div className="grow">
                <div><b>{d.name}</b></div>
                <div className="row small muted" style={{ gap: 6, marginTop: 3 }}>
                  <span>{d.meal_types.map((m) => MEAL_NAMES[m]).join(", ")}</span>
                  <span>· {d.cuisine}</span>
                  {d.protein_label && <span>· białko {d.protein_label}</span>}
                </div>
                <div className="row" style={{ marginTop: 4, gap: 5 }}>
                  {d.invented && <span className="badge ok">wymyślone</span>}
                  <TagBadges tags={d.tags} />
                </div>
              </div>
              <div className="stack" style={{ gap: 4, justifyItems: "end" }}>
                {d.status !== "zatwierdzone" && <span className={`badge ${d.status === "odrzucone" ? "error" : "warn"}`}>{STATUS_NAMES[d.status]}</span>}
                {d.status === "zatwierdzone" && !d.in_pool && (
                  <span className="badge warn" title={d.excluded.join("; ") || "przekąska spoza listy dozwolonych"}>
                    {d.meal_types.includes("przekaska") ? "przekąska wyłączona" : "poza pulą"}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
        <div className="sticky">{dishId ? <DishDetailCard id={dishId} onChanged={list.reload} /> : <div className="card empty">Wybierz danie z listy.</div>}</div>
      </div>
    </div>
  );
}

function DishDetailCard({ id, onChanged }: { id: string; onChanged: () => void }) {
  const toast = useToast();
  const { data: d, error, loading, setData } = useAsync<DishDetail>(() => api.dish(id), [id]);
  if (loading && !d) return <div className="card"><Loading /></div>;
  if (error) return <ErrorBox error={error} />;
  if (!d) return null;

  const setStatus = async (status: string) => {
    try {
      setData(await api.setDishStatus(d.id, status));
      toast(status === "odrzucone" ? "Danie nie będzie trafiać do planów." : "Status zapisany.");
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <section className="card stack">
      <div className="card-head">
        <div>
          <h2>{d.name}</h2>
          <div className="muted small">{d.meal_labels.join(", ")} · kuchnia {d.cuisine}{d.prep_minutes ? ` · ${d.prep_minutes} min` : ""}{d.batch_days ? ` · na ${d.batch_days} dni` : ""}</div>
        </div>
        <span className={`badge ${d.status === "zatwierdzone" ? "ok" : d.status === "odrzucone" ? "error" : "warn"}`}>{STATUS_NAMES[d.status]}</span>
      </div>
      <TagBadges tags={d.tags} isTreat={d.is_treat} isSweet={d.is_sweet} />
      {d.description && <p className="meal-desc">{d.description}</p>}
      {d.excluded.length > 0 && (
        <div className="alert warn small">
          Nie trafia do planów: <ul>{d.excluded.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      <div>
        <h3 style={{ marginBottom: 4 }}>Składniki (typowa porcja przy 2000 kcal)</h3>
        <table className="table">
          <thead>
            <tr><th>Produkt</th><th>Rola</th><th className="right">Typowo</th><th className="right">Zakres</th></tr>
          </thead>
          <tbody>
            {d.ingredients.map((i) => (
              <tr key={i.product_id}>
                <td>
                  <a href={`#/produkty/${i.product_id}`}>{i.name}</a>
                  {i.optional && <span className="muted small"> (opcjonalnie)</span>}
                  {i.portioning !== "osobno" && <div className="muted small">{PORTIONING[i.portioning]}</div>}
                </td>
                <td className="muted">{ROLE_NAMES[i.role] ?? i.role}</td>
                <td className="right nowrap">{num(i.typical_g)} g{i.household && <div className="muted small">≈ {i.household}</div>}</td>
                <td className="right muted nowrap">{i.min_g !== null || i.max_g !== null ? `${num(i.min_g ?? 0)}–${i.max_g !== null ? num(i.max_g) : "…"} g` : "auto"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="muted small">Gramatury w planie dobiera silnik do celów każdej osoby.</div>
      </div>
      {d.groups.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 4 }}>Stałe proporcje</h3>
          {d.groups.map((g) => (
            <div key={g.name} className="group-box">
              <b>{g.name.charAt(0).toUpperCase() + g.name.slice(1)}</b>:{" "}
              {g.items.map((i) => `${i.name.toLowerCase()} ${num(i.share_pct)}%`).join(" · ")}
              <div className="muted small">Silnik może zrobić więcej albo mniej tej masy, ale udziały składników zostają (± {num(g.tolerance_pct)}%).</div>
            </div>
          ))}
        </div>
      )}
      {d.profile && (
        <div className="small">
          <b>Profil:</b> białko z {d.profile.protein_sources.join(", ") || "—"} ({d.profile.protein_label}); węglowodany z {d.profile.carb_sources.join(", ") || "—"}; {d.profile.energy_label}.
          <div className="muted">
            Możliwe białko w porcji: {Object.entries(d.profile.protein_range).map(([p, r]) => `${d.person_names?.[p] ?? p}: ${num(r[0])}–${num(r[1])} g`).join(" · ")}
          </div>
        </div>
      )}
      {d.steps.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 4 }}>Przygotowanie</h3>
          <ol className="steps">{d.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </div>
      )}
      {d.source && <div className="muted small">Źródło: {d.source}</div>}
      <div className="row">
        {d.status !== "zatwierdzone" && <button className="btn primary" onClick={() => setStatus("zatwierdzone")}>Zatwierdź</button>}
        {d.status !== "odrzucone" && <button className="btn danger" onClick={() => setStatus("odrzucone")}>Odrzuć (nie proponuj)</button>}
      </div>
    </section>
  );
}

const INVENT_EXAMPLES = [
  "Trzy kolacje z krewetkami, lekkie",
  "Dwa obiady kuchni włoskiej i zupa na zapas",
  "Śniadania na szybko bez jajek",
  "Coś meksykańskiego na obiad",
];

function InventDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [count, setCount] = useState(6);
  const [jobId, setJobId] = useState<string | null>(null);
  const [done, setDone] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    try {
      setJobId((await api.inventDishes(text.trim(), count)).id);
    } catch (e) {
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Wymyśl nowe dania"
      onClose={onClose}
      footer={
        jobId ? (
          <button className="btn primary" onClick={onClose} disabled={!done}>Zamknij</button>
        ) : (
          <>
            <button className="btn" onClick={onClose}>Anuluj</button>
            <button className="btn primary" onClick={start} disabled={busy || text.trim().length < 3}>{busy ? <Spinner /> : "Wymyśl"}</button>
          </>
        )
      }
    >
      {!jobId && (
        <>
          <div className="muted small">
            Model wymyśli przepisy wyłącznie z produktów z bazy (żeby dało się policzyć gramatury), a program sprawdzi każde
            danie: białko, Twoje zakazy, poprawne nazwy i czy nie dubluje istniejących. Przyjęte trafią do biblioteki
            z oznaczeniem „wymyślone”.
          </div>
          <textarea rows={3} maxLength={600} placeholder="np. trzy lekkie kolacje z krewetkami" value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          <div className="row small">
            {INVENT_EXAMPLES.map((ex) => (
              <button key={ex} className="btn small ghost" onClick={() => setText(ex)}>+ {ex}</button>
            ))}
          </div>
          <label className="field" style={{ width: 200 }}>
            <span>Ile dań najwyżej</span>
            <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
              {[3, 6, 9, 12].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
        </>
      )}
      {jobId && <JobProgress jobId={jobId} onDone={setDone} />}
      {done?.status === "gotowe" && done.result?.dishes && (
        <div className="alert ok">
          Nowe dania:
          <ul>
            {done.result.dishes.map((d) => (
              <li key={d.id}><a href={`#/dania/${d.id}`} onClick={onClose}>{d.name}</a></li>
            ))}
          </ul>
        </div>
      )}
    </Modal>
  );
}
