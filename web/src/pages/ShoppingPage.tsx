import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorBox, Loading, useToast } from "../components/ui";
import type { PlanSummary, Shopping } from "../types";
import { go, loadLocal, num, plural, saveLocal, useAsync } from "../util";

export default function ShoppingPage({ planId }: { planId?: string }) {
  const toast = useToast();
  const plans = useAsync<PlanSummary[]>(() => api.plans(), []);
  const activeId = planId ?? plans.data?.find((p) => p.active)?.id ?? plans.data?.[0]?.id;
  const { data, error, loading, reload } = useAsync<Shopping | null>(
    () => (activeId ? api.shopping(activeId) : Promise.resolve(null)),
    [activeId],
  );
  // odhaczenia trzymamy dla całej rodziny planu — zamiana dania (nowa wersja) nie kasuje listy
  const family = plans.data?.find((p) => p.id === activeId)?.family_id ?? activeId;
  const storageKey = `zakupy:${family}`;
  const [done, setDone] = useState<string[]>([]);
  const [hideDone, setHideDone] = useState(false);

  useEffect(() => {
    setDone(loadLocal<string[]>(storageKey, []));
  }, [storageKey]);

  const toggle = (pid: string) => {
    const next = done.includes(pid) ? done.filter((x) => x !== pid) : [...done, pid];
    setDone(next);
    saveLocal(storageKey, next);
  };

  const copy = async () => {
    if (!data) return;
    const lines: string[] = [`Lista zakupów — ${data.title}`];
    for (const g of data.groups) {
      lines.push("", g.label.toUpperCase());
      for (const it of g.items) {
        if (done.includes(it.produkt)) continue;
        const store = it.sklep ? ` [${it.sklep.sklep}: ${it.sklep.nazwa}${it.sklep.marka ? `, ${it.sklep.marka}` : ""}${it.opakowania_tekst ? ` — ${it.opakowania_tekst}` : ""}]` : "";
        lines.push(`- ${it.nazwa}${it.ilosc ? ` — ${it.ilosc}` : ""}${store}`);
      }
    }
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      toast("Skopiowano listę do schowka.");
    } catch {
      toast("Nie udało się skopiować (brak dostępu do schowka).", "error");
    }
  };

  if (plans.loading && !plans.data) return <div className="page"><Loading /></div>;
  if (plans.error && !plans.data) return <div className="page"><ErrorBox error={plans.error} onRetry={plans.reload} /></div>;
  if (!activeId)
    return (
      <div className="page">
        <div className="card empty">
          <div className="big">🛒</div>
          <h2>Brak planu</h2>
          <p>Lista zakupów powstaje z planu tygodnia.</p>
          <button className="btn primary" onClick={() => go("nowy")}>Ułóż plan</button>
        </div>
      </div>
    );

  const total = data?.groups.reduce((s, g) => s + g.items.length, 0) ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Lista zakupów</h1>
          {data && (
            <div className="sub">
              {data.title} · {data.days} {plural(data.days, "dzień", "dni", "dni")} · {data.persons} {plural(data.persons, "osoba", "osoby", "osób")} · odhaczone {done.length} z {total}
            </div>
          )}
        </div>
        <div className="row no-print">
          <select value={activeId} onChange={(e) => go("zakupy", e.target.value)}>
            {(plans.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.title} (wersja {p.version}){p.active ? " — aktualny" : ""}
              </option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} /> Ukryj kupione
          </label>
          <button className="btn" onClick={copy}>Kopiuj</button>
          <button className="btn" onClick={() => window.print()}>Drukuj</button>
          <button className="btn" onClick={() => { setDone([]); saveLocal(storageKey, []); }}>Wyczyść odhaczenia</button>
        </div>
      </div>
      <div className="alert info small no-print">
        Ilości w stanie jak w przepisach: ryż i makaron suche, mięso surowe, ziemniaki obrane. Podpowiedzi 🛒 to produkty z półek Lidla (i Kauflandu, gdy w Lidlu brak) w Rumunii — sprawdzone w serwisie Monitorul Prețurilor.
      </div>
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <div className="stack">
          {data.groups.map((g) => {
            const items = g.items.filter((it) => !(hideDone && done.includes(it.produkt)));
            if (!items.length) return null;
            return (
              <section key={g.category} className="card">
                <h3 style={{ marginBottom: 4 }}>{g.label}</h3>
                {items.map((it) => (
                  <label key={it.produkt} className={`shop-item ${done.includes(it.produkt) ? "done" : ""}`}>
                    <input type="checkbox" checked={done.includes(it.produkt)} onChange={() => toggle(it.produkt)}
                      aria-label={`${it.nazwa}${it.na_wynos ? " — do zamówienia" : " — kupione"}`} />
                    <div>
                      <div className="shop-name">{it.nazwa}</div>
                      {it.sklep && (
                        <div className="shop-store">
                          🛒 {it.sklep.sklep}: <i>{it.sklep.nazwa}</i>
                          {it.sklep.marka ? `, ${it.sklep.marka}` : ""}
                          {it.opakowania_tekst ? ` — ${it.opakowania_tekst}` : ""}
                          {it.sklep.uwagi ? <span className="muted"> · {it.sklep.uwagi}</span> : null}
                        </div>
                      )}
                    </div>
                    <div className="right nowrap">
                      {it.pomijalny ? <span className="muted">do smaku</span> : <b>{it.ilosc}</b>}
                      {!it.pomijalny && /(ml|l)$/.test(it.ilosc) && <div className="muted small">{num(it.gramy)} g</div>}
                    </div>
                  </label>
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
