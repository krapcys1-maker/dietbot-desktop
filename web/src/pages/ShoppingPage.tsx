import { useEffect, useState } from "react";
import { api } from "../api";
import ExportButtons from "../components/ExportButtons";
import MoreActions from "../components/MoreActions";
import Icon from "../components/Icon";
import { ErrorBox, Loading, useToast } from "../components/ui";
import type { PlanSummary, Shopping } from "../types";
import { go, loadLocal, plural, saveLocal, useAsync } from "../util";

export default function ShoppingPage({ planId }: { planId?: string }) {
  const toast = useToast();
  const plans = useAsync<PlanSummary[]>(() => api.plans(), []);
  const activeId = planId ?? plans.data?.find((p) => p.active)?.id ?? plans.data?.[0]?.id;
  const { data: response, error, loading, reload } = useAsync<Shopping | null>(
    () => (activeId ? api.shopping(activeId) : Promise.resolve(null)),
    [activeId],
  );
  const data = response?.plan_id === activeId ? response : null;
  // odhaczenia trzymamy dla całej rodziny planu — zamiana dania (nowa wersja) nie kasuje listy
  const family = plans.data?.find((p) => p.id === activeId)?.family_id ?? activeId;
  // Stare odhaczenie jednego wariantu nie oznacza zakupu całej połączonej pozycji.
  const storageKey = `zakupy:ilosci:${family}`;
  const [bought, setBought] = useState<Record<string, number>>({});
  const done = data?.groups.flatMap(g => g.items).filter(it => (bought[it.produkt] ?? 0) >= it.gramy).map(it => it.produkt) ?? [];
  const [hideDone, setHideDone] = useState(false);

  useEffect(() => {
    setBought(loadLocal<Record<string, number>>(storageKey, {}));
  }, [storageKey]);

  const toggle = (pid: string) => {
    const grams = data?.groups.flatMap(g => g.items).find(it => it.produkt === pid)?.gramy;
    if (grams == null) return;
    const next = { ...bought };
    if (done.includes(pid)) delete next[pid]; else next[pid] = grams;
    setBought(next);
    saveLocal(storageKey, next);
  };

  const copy = async () => {
    if (!data) return;
    const lines: string[] = [`Lista zakupów — ${data.title}`];
    for (const g of data.groups) {
      lines.push("", g.label.toUpperCase());
      for (const it of g.items) {
        if (done.includes(it.produkt)) continue;
        // bez „- ” i „[ ]” — notatki w telefonie same robią z wierszy listę do odhaczania
        const part = bought[it.produkt] ?? 0;
        const remaining = Math.round((it.gramy - part) * 10) / 10;
        const variants = it.warianty_rozne && it.warianty ? ` (w tym: ${it.warianty.map(v => `${v.nazwa.toLowerCase()} ${Math.ceil(v.gramy / 10) * 10} g`).join(", ")})` : "";
        lines.push(part > 0 ? `${it.nazwa} — jeszcze ${remaining} g` : `${it.nazwa} — ${it.ilosc}${variants}`);
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
          <Icon name="basket" />
          <h2>Brak planu</h2>
          <p>Lista zakupów powstaje z planu tygodnia.</p>
          <button className="btn primary" onClick={() => go("nowy")}>Ułóż plan</button>
        </div>
      </div>
    );

  const total = data?.groups.reduce((s, g) => s + g.items.length, 0) ?? 0;
  const doneCount = data?.groups.reduce((s, g) => s + g.items.filter((it) => done.includes(it.produkt)).length, 0) ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">WSZYSTKO NA JEDNEJ LIŚCIE</span><h1>Lista zakupów</h1>
          {data && (
            <div className="sub">
              {data.title} · {data.days} {plural(data.days, "dzień", "dni", "dni")} · {data.persons} {plural(data.persons, "osoba", "osoby", "osób")} · odhaczone {doneCount} z {total}
            </div>
          )}
        </div>
      </div>
      <div className="plan-toolbar no-print">
        <ExportButtons key={activeId} planId={activeId} shoppingOnly />
        <MoreActions>
          <button className="btn" onClick={() => go("tydzien", activeId)}>Otwórz dietę</button>
          <select aria-label="Plan do zakupów" value={activeId} onChange={(e) => go("zakupy", e.target.value)}>
            {(plans.data ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.title} (wersja {p.version}){p.active ? " — aktualny" : ""}
              </option>
            ))}
          </select>
          <button className="btn" onClick={copy}>Kopiuj pozostałe zakupy</button>
          <button className="btn" disabled={!Object.keys(bought).length} onClick={() => { if (window.confirm("Wyczyścić odhaczenia tej listy?")) { setBought({}); saveLocal(storageKey, {}); } }}>Wyczyść odhaczenia</button>
        </MoreActions>
      </div>
      <div className="card flat row no-print">
        <div className="grow"><b>{doneCount === total && total > 0 ? "Wszystko kupione" : `Pozostało ${total - doneCount} z ${total} ${plural(total, "produktu", "produktów", "produktów")}`}</b>
          <div className="muted small">Eksport obejmuje całą listę. Odhaczenie zapamiętuje ilość — po zmianie diety większy zakup pojawi się ponownie.</div></div>
        <label className="check"><input type="checkbox" checked={hideDone} onChange={(e) => setHideDone(e.target.checked)} /> Ukryj kupione</label>
        <div className="shopping-progress" role="progressbar" aria-label="Kupione produkty" aria-valuemin={0} aria-valuemax={total || 1} aria-valuenow={doneCount}><span style={{ width: `${total ? doneCount / total * 100 : 0}%` }} /></div>
      </div>
      <div className="alert info small no-print">
        Jedna pozycja na produkt, warianty zsumowane. Bez sklepów, przypraw i ziół.
        Ryż i makaron ważone na sucho, mięso surowe, ziemniaki obrane.
        <div className="muted">Zakup innego wariantu niż w przepisie może zmienić rzeczywiste kcal i makro.</div>
      </div>
      {loading && !data && <Loading />}
      {error && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <div className="shopping-groups">
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
                      {(it.warianty?.length ?? 0) > 1 && (
                        <div className={`small ${it.warianty_rozne ? "warn-text" : "muted"}`}
                          title={it.warianty_rozne ? "Warianty różnią się kalorycznością — kup je w tych proporcjach, inaczej plan się zmieni." : undefined}>
                          w tym: {it.warianty!.map(v => `${v.nazwa.toLowerCase()} ${Math.ceil(v.gramy / 10) * 10} g`).join(", ")}
                        </div>
                      )}
                      {(bought[it.produkt] ?? 0) > 0 && !done.includes(it.produkt) && <div className="muted small">Kupiono {bought[it.produkt]} g · dokup {Math.round((it.gramy - bought[it.produkt]) * 10) / 10} g</div>}
                    </div>
                    <div className="right nowrap">
                      <b>{it.ilosc}</b>
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
