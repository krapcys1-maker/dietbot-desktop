import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import GoalCalculator from "../components/GoalCalculator";
import { ErrorBox, JobProgress, Loading, Modal, Spinner, useToast } from "../components/ui";
import type { AssumptionsData, AssumptionsResponse, BandData, PlanSummary } from "../types";
import { DAY_NAMES, go, useAsync } from "../util";

const MACROS: { key: "kcal" | "bialko" | "tluszcz" | "weglowodany"; label: string; unit: string }[] = [
  { key: "kcal", label: "Energia", unit: "kcal" },
  { key: "bialko", label: "Białko", unit: "g" },
  { key: "tluszcz", label: "Tłuszcz", unit: "g" },
  { key: "weglowodany", label: "Węglowodany", unit: "g" },
];
const DAY_KEYS = ["poniedzialek", "wtorek", "sroda", "czwartek", "piatek", "sobota", "niedziela"];
const CARB_CATS = [
  { id: "ryz", name: "ryż" }, { id: "makaron", name: "makaron" }, { id: "ziemniaki", name: "ziemniaki" }, { id: "pieczywo", name: "pieczywo" },
  { id: "kasze", name: "kasze" }, { id: "maka", name: "mąka i ciasto" }, { id: "platki", name: "płatki" }, { id: "straczki", name: "strączki" },
];
const CUISINE_NAMES: Record<string, string> = {
  polska: "polska", wloska: "włoska", azjatycka: "azjatycka", miedzynarodowa: "międzynarodowa", wege: "wege", hiszpanska: "hiszpańska",
  indyjska: "indyjska", meksykanska: "meksykańska", grecka: "grecka", francuska: "francuska", srodziemnomorska: "śródziemnomorska",
  amerykanska: "amerykańska", turecka: "turecka", bliskowschodnia: "bliskowschodnia", rumunska: "rumuńska", slaska: "śląska",
};

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const toNum = (v: string) => (v.trim() === "" ? NaN : Number(v.replace(",", ".")));

export default function AssumptionsPage() {
  const toast = useToast();
  const { data: resp, error, loading, reload } = useAsync<AssumptionsResponse>(() => api.assumptions(), []);
  const [data, setData] = useState<AssumptionsData | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [job, setJob] = useState<string | null>(null);
  const plans = useAsync<PlanSummary[]>(() => api.plans(), [saved]);

  useEffect(() => {
    if (resp) {
      setData(clone(resp.data));
      setWarnings(resp.warnings);
    }
  }, [resp]);

  const dirty = useMemo(() => !!resp && !!data && JSON.stringify(resp.data) !== JSON.stringify(data), [resp, data]);
  const productName = useMemo(() => Object.fromEntries((resp?.products ?? []).map((p) => [p.id, p.name])), [resp]);

  if (loading && !resp) return <div className="page"><Loading /></div>;
  if (error) return <div className="page"><ErrorBox error={error} onRetry={reload} /></div>;
  if (!resp || !data) return null;

  const update = (fn: (d: AssumptionsData) => void) => {
    setData((prev) => {
      const next = clone(prev!);
      fn(next);
      return next;
    });
    setSaved(false);
  };

  const validate = async () => {
    try {
      const r = await api.checkAssumptions(data);
      setErrors(r.errors);
      setWarnings(r.warnings);
      if (r.ok) toast("Założenia są spójne.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const recompute = async (planId: string, repair: boolean) => {
    try {
      setJob((await api.recompute(planId, repair)).id);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const r = await api.saveAssumptions(data);
      setErrors(r.errors);
      setWarnings(r.warnings);
      if (r.ok) {
        toast("Zapisano założenia.");
        setSaved(true);
        reload();
      } else {
        toast("Nie zapisano — popraw błędy.", "error");
      }
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };

  const active = plans.data?.find((p) => p.active);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <span className="eyebrow">PO TWOJEMU</span><h1>Cele i preferencje</h1>
          <div className="sub">Twój sposób jedzenia, cele i ulubione smaki. Zmiany zaczną działać po zapisaniu — dotychczasowe diety pozostaną bez zmian.</div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => { setData(clone(resp.data)); setErrors([]); }} disabled={!dirty}>Cofnij zmiany</button>
          <button className="btn" onClick={validate}>Sprawdź</button>
          <button className="btn primary" onClick={save} disabled={saving || !dirty}>{saving ? <Spinner /> : "Zapisz"}</button>
        </div>
      </div>

      {errors.length > 0 && <div className="alert error">Błędy:<ul>{errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
      {warnings.length > 0 && <div className="alert warn">Uwagi:<ul>{warnings.map((e, i) => <li key={i}>{e}</li>)}</ul></div>}
      {saved && active && (
        <div className="alert ok row">
          <span className="grow">Aktualny plan „{active.title}” był liczony według poprzednich założeń.</span>
          <button className="btn small" onClick={() => recompute(active.id, false)}>Przelicz te same dania</button>
          <button className="btn small" onClick={() => recompute(active.id, true)}>Przelicz i napraw</button>
        </div>
      )}

      <section className="card stack">
        <h2>Rodzaj diety</h2>
        <label className="field"><span>Wspólny jadłospis dla wszystkich osób</span>
          <select value={data.rodzaj_diety ?? "tradycyjna"} onChange={e => update(d => { d.rodzaj_diety = e.target.value as AssumptionsData["rodzaj_diety"]; })}>
            <option value="tradycyjna">Tradycyjna — wszystkie produkty</option><option value="wege">Wegetariańska — bez mięsa i ryb</option><option value="wegan">Wegańska — bez składników odzwierzęcych</option>
          </select>
        </label>
        {data.rodzaj_diety && data.rodzaj_diety !== "tradycyjna" && <>
          <p className="muted">Sprawdzamy składniki, przekąski i stałe pozycje. Produkty złożone o nieznanym składzie są pomijane. Wybieraj pieczywo i makaron bez jaj/mleka; nazwa „wege” na daniu nie wystarcza. To filtr składników, nie audyt wszystkich witamin i minerałów diety.</p>
          {data.rodzaj_diety === "wegan" && <p className="alert info small">Przy diecie wegańskiej trzeba zadbać m.in. o wiarygodne źródło witaminy B12. Porcja nie bilansuje witamin ani minerałów. <a href="https://www.nhs.uk/live-well/eat-well/how-to-eat-a-balanced-diet/the-vegan-diet/" target="_blank" rel="noreferrer">Informacje NHS o diecie wegańskiej</a>.</p>}
          {(data.dzien.stale_pozycje ?? []).filter(f => !resp.products.find(p => p.id === f.produkt)?.diets.includes(data.rodzaj_diety!)).map(f => <div className="alert warn row" key={f.produkt}>
            <span className="grow">Stała pozycja „{productName[f.produkt]}” nie pasuje do wybranej diety. Zmień ją w sekcji Dzień albo usuń.</span>
            <button className="btn small" onClick={() => update(d => { d.dzien.stale_pozycje = d.dzien.stale_pozycje?.filter(x => x.produkt !== f.produkt); })}>Usuń tę stałą pozycję</button>
          </div>)}
          <p className="muted small">Niezgodne przekąski są pomijane przy układaniu; zaznaczenia pozostają do powrotu do diety tradycyjnej.</p>
          <button className="btn" onClick={() => update(d => { const s = (d.dzien.przekaski ??= {}); s.dozwolone = [...new Set([...(s.dozwolone ?? []), ...resp.snack_options.filter(x => x.id.startsWith("roslinne_020_") && x.diets.includes(d.rodzaj_diety!)).map(x => x.id)])]; })}>Dodaj nowe zgodne przekąski do dozwolonych</button>
        </>}
      </section>

      {data.osoby.map((person, pi) => (
        <section key={person.id} className="card stack">
          <div className="card-head">
            <h2>{person.nazwa}</h2>
            <label className="field" style={{ width: 220 }}>
              <span>Nazwa wyświetlana</span>
              <input type="text" value={person.nazwa} onChange={(e) => update((d) => { d.osoby[pi].nazwa = e.target.value; })} />
            </label>
          </div>
          <GoalCalculator key={data.dzien.posilki.length} meals={data.dzien.posilki.length} onApply={r => update(d => {
            d.osoby[pi].cele_dzienne = r.targets;
            d.osoby[pi].kotwica_bialka_na_posilek = r.protein_anchor;
          })} />
          <div className="table-scroll"><table className="band-table">
            <thead>
              <tr><th>Dziennie</th><th>min</th><th>cel</th><th>max</th><th /></tr>
            </thead>
            <tbody>
              {MACROS.map((m) => (
                <BandRow key={m.key} label={m.label} unit={m.unit} band={person.cele_dzienne[m.key]}
                  onChange={(b) => update((d) => { d.osoby[pi].cele_dzienne[m.key] = b; })} />
              ))}
              <BandRow label="Białko na posiłek" unit="g" band={person.kotwica_bialka_na_posilek} noTarget
                onChange={(b) => update((d) => { d.osoby[pi].kotwica_bialka_na_posilek = b; })} />
            </tbody>
          </table></div>
          <div className="muted small">
            Kalorie pochodzą z tabel/etykiet produktów. Cele białka, tłuszczów i węglowodanów są sprawdzane osobno.
          </div>
          <details className="disclosure"><summary>Zaawansowane ustawienia porcji</summary><div className="row">
            <label className="field">
              <span>Tolerancja białka na posiłek [± g]</span>
              <input type="number" min={0} max={30} value={person.tolerancja_kotwicy_g ?? 5} style={{ width: 90 }}
                onChange={(e) => update((d) => { d.osoby[pi].tolerancja_kotwicy_g = toNum(e.target.value); })} />
            </label>
            {resp.meals.map((m) => (
              <label key={m.id} className="field" title="Suma gramów wszystkich składników posiłku (np. zupa z pieczywem)">
                <span>Maks. talerz — {m.name.toLowerCase()} [g]</span>
                <input type="number" min={150} max={3000} style={{ width: 100 }} placeholder="auto"
                  value={person.maks_masa_posilku_g?.[m.id] ?? ""}
                  onChange={(e) => update((d) => {
                    const caps = { ...(d.osoby[pi].maks_masa_posilku_g ?? {}) };
                    if (e.target.value.trim() === "") delete caps[m.id];
                    else caps[m.id] = toNum(e.target.value);
                    d.osoby[pi].maks_masa_posilku_g = caps;
                  })} />
              </label>
            ))}
          </div>
          </details>
          <ProductChips label="Nie je (tylko ta osoba)" ids={person.wyklucza_produkty ?? []} products={resp.products} names={productName}
            onChange={(ids) => update((d) => { d.osoby[pi].wyklucza_produkty = ids; })} />
        </section>
      ))}

      <section className="card stack">
        <h2>Dzień</h2>
        <div className="row" style={{ gap: 20 }}>
          <div className="field">
            <span>Posiłki</span>
            <div className="row">
              {resp.meals.map((m) => (
                <label key={m.id} className="check">
                  <input type="checkbox" checked={data.dzien.posilki.includes(m.id)}
                    onChange={(e) => update((d) => {
                      const set = new Set(d.dzien.posilki);
                      if (e.target.checked) set.add(m.id); else set.delete(m.id);
                      d.dzien.posilki = resp.meals.map((x) => x.id).filter((x) => set.has(x));
                    })} />
                  {m.name}
                </label>
              ))}
            </div>
          </div>
          <label className="check">
            <input type="checkbox" checked={data.dzien.kolacja_lzejsza_niz_obiad ?? true}
              onChange={(e) => update((d) => { d.dzien.kolacja_lzejsza_niz_obiad = e.target.checked; })} />
            Kolacja lżejsza niż obiad
          </label>
        </div>
        <div className="field">
          <span>Stałe pozycje każdego dnia (dla każdej osoby)</span>
          <div className="table-scroll"><table className="table fixed-items-table">
            <thead><tr><th>Produkt</th><th>Gramy</th><th>Opis</th><th /></tr></thead>
            <tbody>
              {(data.dzien.stale_pozycje ?? []).map((fx, i) => (
                <tr key={i}>
                  <td>
                    <select value={fx.produkt} onChange={(e) => update((d) => { d.dzien.stale_pozycje![i].produkt = e.target.value; })}>
                      {resp.products.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                    </select>
                  </td>
                  <td><input type="number" value={fx.gramy} style={{ width: 90 }} onChange={(e) => update((d) => { d.dzien.stale_pozycje![i].gramy = toNum(e.target.value); })} /></td>
                  <td><input type="text" value={fx.opis ?? ""} onChange={(e) => update((d) => { d.dzien.stale_pozycje![i].opis = e.target.value; })} /></td>
                  <td><button className="btn small ghost" onClick={() => update((d) => { d.dzien.stale_pozycje!.splice(i, 1); })}>Usuń</button></td>
                </tr>
              ))}
            </tbody>
          </table></div>
          <div><button className="btn small" onClick={() => update((d) => { (d.dzien.stale_pozycje ??= []).push({ produkt: resp.products[0].id, gramy: 10, opis: "" }); })}>+ Dodaj pozycję</button></div>
        </div>
        <div className="row" style={{ gap: 20, alignItems: "flex-start" }}>
          <label className="field">
            <span>Przekąski bilansujące na osobę dziennie (maks.)</span>
            <input type="number" min={0} max={3} style={{ width: 90 }} value={data.dzien.przekaski?.max_na_osobe_dziennie ?? 1}
              onChange={(e) => update((d) => { (d.dzien.przekaski ??= {}).max_na_osobe_dziennie = toNum(e.target.value); })} />
          </label>
          <div className="field">
            <span>Dozwolone przekąski</span>
            <div className="row">
              {resp.snack_options.map((s) => (
                <label key={s.id} className="check">
                  <input type="checkbox" checked={(data.dzien.przekaski?.dozwolone ?? []).includes(s.id)}
                    onChange={(e) => update((d) => {
                      const p = (d.dzien.przekaski ??= {});
                      const set = new Set(p.dozwolone ?? []);
                      if (e.target.checked) set.add(s.id); else set.delete(s.id);
                      p.dozwolone = resp.snack_options.map((x) => x.id).filter((x) => set.has(x));
                    })} />
                  {s.name}
                </label>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="card stack">
        <h2>Tydzień</h2>
        <div className="row" style={{ gap: 20 }}>
          <label className="field">
            <span>Posiłki z puli 20% — min</span>
            <input type="number" min={0} max={21} style={{ width: 90 }} value={data.tydzien.posilki_20_procent.min}
              onChange={(e) => update((d) => { d.tydzien.posilki_20_procent.min = toNum(e.target.value); })} />
          </label>
          <label className="field">
            <span>— max</span>
            <input type="number" min={0} max={21} style={{ width: 90 }} value={data.tydzien.posilki_20_procent.max}
              onChange={(e) => update((d) => { d.tydzien.posilki_20_procent.max = toNum(e.target.value); })} />
          </label>
          <label className="field">
            <span>Słodkie kolacje (maks.)</span>
            <input type="number" min={0} max={7} style={{ width: 90 }} value={data.tydzien.slodkie_kolacje_max ?? 2}
              onChange={(e) => update((d) => { d.tydzien.slodkie_kolacje_max = toNum(e.target.value); })} />
          </label>
          <label className="check">
            <input type="checkbox" checked={data.tydzien.bez_powtorek ?? true} onChange={(e) => update((d) => { d.tydzien.bez_powtorek = e.target.checked; })} />
            Bez powtórek dań
          </label>
        </div>
        <div className="field">
          <span>Gotowanie na zapas (to samo danie przez kilka kolejnych dni)</span>
          {(data.tydzien.na_zapas ?? []).map((rule, i) => (
            <div key={i} className="row">
              <select value={rule.tag} onChange={(e) => update((d) => { d.tydzien.na_zapas![i].tag = e.target.value; })}>
                {resp.tags.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select value={rule.posilek} onChange={(e) => update((d) => { d.tydzien.na_zapas![i].posilek = e.target.value; })}>
                {resp.meals.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
              {DAY_KEYS.map((dk, di) => (
                <label key={dk} className="check">
                  <input type="checkbox" checked={rule.dni.includes(dk)}
                    onChange={(e) => update((d) => {
                      const set = new Set(d.tydzien.na_zapas![i].dni);
                      if (e.target.checked) set.add(dk); else set.delete(dk);
                      d.tydzien.na_zapas![i].dni = DAY_KEYS.filter((x) => set.has(x));
                    })} />
                  {DAY_NAMES[di].slice(0, 3)}
                </label>
              ))}
              <button className="btn small ghost" onClick={() => update((d) => { d.tydzien.na_zapas!.splice(i, 1); })}>Usuń</button>
            </div>
          ))}
          <div><button className="btn small" onClick={() => update((d) => { (d.tydzien.na_zapas ??= []).push({ tag: "zupa", posilek: "obiad", dni: ["wtorek", "sroda"] }); })}>+ Dodaj zasadę</button></div>
        </div>
      </section>

      <section className="card stack">
        <h2>Zakazy i kuchnie</h2>
        <ProductChips label="Produkty zakazane (obie osoby)" ids={data.zakazy?.produkty ?? []} products={resp.products} names={productName}
          onChange={(ids) => update((d) => { (d.zakazy ??= {}).produkty = ids; })} />
        <div className="field">
          <span>Tagi dań zakazane w posiłkach</span>
          {resp.meals.map((m) => (
            <div key={m.id} className="row">
              <b style={{ width: 90 }}>{m.name}</b>
              {["slodkie", "pula_20", "zupa", "na_cieplo"].map((t) => (
                <label key={t} className="check">
                  <input type="checkbox" checked={(data.zakazy?.tagi_dan_w_posilkach?.[m.id] ?? []).includes(t)}
                    onChange={(e) => update((d) => {
                      const z = (d.zakazy ??= {});
                      const map = (z.tagi_dan_w_posilkach ??= {});
                      const set = new Set(map[m.id] ?? []);
                      if (e.target.checked) set.add(t); else set.delete(t);
                      if (set.size) map[m.id] = [...set]; else delete map[m.id];
                    })} />
                  {t.replace("_", " ")}
                </label>
              ))}
            </div>
          ))}
        </div>
        <CategoryChecks label="Dozwolone główne źródła węglowodanów (pula 80%)" value={data.zakazy?.dozwolone_zrodla_weglowodanow ?? []}
          onChange={(v) => update((d) => { (d.zakazy ??= {}).dozwolone_zrodla_weglowodanow = v; })} />
        <CategoryChecks label="Kolacja bez tych węglowodanów (żeby nie była obiadem)" value={data.zakazy?.kolacja_bez_weglowodanow_z ?? []}
          onChange={(v) => update((d) => { (d.zakazy ??= {}).kolacja_bez_weglowodanow_z = v; })} />
        <div className="field">
          <span>Kuchnie</span>
          <div className="row">
            {resp.cuisines.map((c) => (
              <label key={c} className="check">
                <input type="checkbox" checked={(data.kuchnie ?? []).includes(c)}
                  onChange={(e) => update((d) => {
                    const set = new Set(d.kuchnie ?? []);
                    if (e.target.checked) set.add(c); else set.delete(c);
                    d.kuchnie = resp.cuisines.filter((x) => set.has(x));
                  })} />
                {CUISINE_NAMES[c] ?? c}
              </label>
            ))}
          </div>
        </div>
        <div className="field">
          <span>Kalorie z tabel/etykiet</span>
          <p className="muted">Cała aplikacja sumuje pole kcal produktów według gramatur. Skrót 4·B + 9·T + 4·W
            nie zastępuje kalorii z tabel, bo pomija m.in. energię błonnika. Obowiązuje to także w podsumowaniach i eksportach.</p>
        </div>
      </section>

      {job && (
        <Modal title="Przeliczanie planu" onClose={() => setJob(null)}>
          <JobProgress jobId={job} onDone={(j) => j.result?.plan_id && setTimeout(() => go("tydzien", j.result!.plan_id!), 800)} />
        </Modal>
      )}
    </div>
  );
}

function BandRow({ label, unit, band, onChange, noTarget }: { label: string; unit: string; band: BandData; onChange: (b: BandData) => void; noTarget?: boolean }) {
  const set = (k: "min" | "cel" | "max", v: string) => {
    const next = { ...band };
    if (k === "cel" && v.trim() === "") delete next.cel;
    else next[k] = toNum(v);
    onChange(next);
  };
  return (
    <tr>
      <td>{label}</td>
      <td><input type="number" aria-label={`${label} — minimum (${unit})`} value={Number.isNaN(band.min) ? "" : band.min} onChange={(e) => set("min", e.target.value)} /></td>
      <td>{noTarget ? <span className="muted">—</span> : <input type="number" aria-label={`${label} — cel (${unit})`} value={band.cel ?? ""} placeholder="środek" onChange={(e) => set("cel", e.target.value)} />}</td>
      <td><input type="number" aria-label={`${label} — maksimum (${unit})`} value={Number.isNaN(band.max) ? "" : band.max} onChange={(e) => set("max", e.target.value)} /></td>
      <td className="muted">{unit}</td>
    </tr>
  );
}

function ProductChips({ label, ids, products, names, onChange }: {
  label: string; ids: string[]; products: { id: string; name: string }[]; names: Record<string, string>; onChange: (ids: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const matches = q.length >= 2 ? products.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()) && !ids.includes(p.id)).slice(0, 8) : [];
  return (
    <div className="field">
      <span>{label}</span>
      <div className="chips">
        {ids.length === 0 && <span className="muted small">brak</span>}
        {ids.map((id) => (
          <span key={id} className="chip">
            {names[id] ?? id}
            <button onClick={() => onChange(ids.filter((x) => x !== id))} aria-label="Usuń">✕</button>
          </span>
        ))}
      </div>
      <input type="search" placeholder="Dodaj produkt…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
      {matches.length > 0 && (
        <div className="row">
          {matches.map((p) => (
            <button key={p.id} className="btn small" onClick={() => { onChange([...ids, p.id]); setQ(""); }}>+ {p.name}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryChecks({ label, value, onChange }: { label: string; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="field">
      <span>{label}</span>
      <div className="row">
        {CARB_CATS.map((c) => (
          <label key={c.id} className="check">
            <input type="checkbox" checked={value.includes(c.id)}
              onChange={(e) => onChange(e.target.checked ? [...value, c.id] : value.filter((x) => x !== c.id))} />
            {c.name}
          </label>
        ))}
      </div>
    </div>
  );
}
