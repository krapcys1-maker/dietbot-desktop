import { useEffect, useState } from "react";
import { api } from "../api";
import Icon from "../components/Icon";
import TasteProposals from "../components/TasteProposals";
import { ErrorBox, Loading, Spinner, useToast } from "../components/ui";
import type { NoteAnalysis, NotebookView, ProductListItem, RuleInput, RuleKind } from "../types";
import { dateTime, go, useAsync } from "../util";

const EXAMPLES = [
  "Za dużo ryżu na obiad — wolę mniej, a w zamian więcej mięsa.",
  "Nie dawaj samych białek jaj, jemy całe jajka.",
  "Lubimy kuchnię azjatycką, mniej dań z jajkami na kolację.",
];

export default function NotebookPage() {
  const toast = useToast();
  const nb = useAsync<NotebookView>(() => api.notebook(), []);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<NoteAnalysis | null>(null);

  const update = (view: NotebookView) => nb.setData(view);
  const run = async (key: string, action: () => Promise<NotebookView>, done?: string) => {
    if (busy) return;
    setBusy(key);
    try { update(await action()); if (done) toast(done); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(null); }
  };

  const send = async () => {
    const msg = text.trim();
    if (!msg || sending) return;
    setSending(true);
    setAnalysis(null);
    try {
      const view = await api.addNote(msg);
      update(view);
      setAnalysis(view.analysis ?? view.notes[0]?.analysis ?? null);
      setText("");
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setSending(false); }
  };

  const copyProgramNotes = async () => {
    const items = (nb.data?.program_notes ?? []).filter(n => n.status === "nowa");
    if (!items.length) { toast("Nie ma otwartych uwag do programu."); return; }
    try {
      await navigator.clipboard.writeText(items.map(n => n.text).join("\n"));
      toast("Skopiowano uwagi do programu.");
    } catch { toast("Nie udało się skopiować (brak dostępu do schowka).", "error"); }
  };

  const saveFile = async () => {
    try {
      const r = await api.saveNotebookFile();
      toast(r.saved ? `Zapisano: ${r.path}` : "Anulowano zapis.");
    } catch (e) { toast((e as Error).message, "error"); }
  };

  if (nb.loading && !nb.data) return <div className="page"><Loading /></div>;
  if (nb.error && !nb.data) return <div className="page"><ErrorBox error={nb.error} onRetry={nb.reload} /></div>;
  const v = nb.data!;
  const active = v.rules.filter(r => r.active);
  const openNotes = v.program_notes.filter(n => n.status === "nowa");

  return <div className="page stack">
    <div className="page-head"><div>
      <span className="eyebrow">PORCJA UCZY SIĘ TWOJEGO GUSTU</span><h1>Notatnik preferencji</h1>
      <div className="sub">Napisz, co Ci nie pasuje albo co lubisz. Zasady trafiają do każdego nowego planu — liczby w nich
        pochodzą z Twojej uwagi albo liczy je program. Uwagi do działania aplikacji zbieramy osobno, dla twórcy.</div>
    </div><button className="btn" onClick={() => void saveFile()}><Icon name="download" />Zapisz notatnik do TXT</button></div>

    <TasteProposals />

    <section className="card stack">
      <div className="card-head"><h2>Nowa uwaga</h2>
        {!v.llm_available && <span className="badge warn">bez AI — zapiszę samą notatkę</span>}</div>
      <textarea rows={5} value={text} maxLength={2000} aria-label="Twoja uwaga"
        placeholder="Np. „Na obiad za dużo ryżu — wolałbym 65 g żonie i 105 g mnie, w zamian więcej mięsa”."
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); void send(); } }} />
      <div className="row">
        <button className="btn primary" disabled={sending || !text.trim()} onClick={() => void send()}>
          <Icon name="notebook" />{sending ? "Analizuję…" : "Zapisz i przeanalizuj"}
        </button>
        {sending && <Spinner label="Model czyta uwagę, program sprawdza produkty i liczby…" />}
        {!text && !sending && EXAMPLES.map(x => <button key={x} className="btn small ghost" onClick={() => setText(x)}>{x}</button>)}
      </div>
      {analysis && <AnalysisBox analysis={analysis} />}
    </section>

    <section className="card stack">
      <div className="card-head"><h2>Zasady stosowane w planach <span className="muted">({active.length})</span></h2></div>
      {v.problems.length > 0 && <div className="alert warn"><b>Pominięte zasady</b><ul>{v.problems.map(p => <li key={p}>{p}</li>)}</ul></div>}
      {!v.rules.length && <div className="muted">Nie ma jeszcze zasad. Napisz uwagę powyżej albo dodaj zasadę ręcznie.</div>}
      <div className="list">
        {v.rules.map(r => <div key={r.id} className="list-item nb-item" style={{ opacity: r.active ? 1 : 0.6 }}>
          <div className="grow">
            <div className="row"><span className={`badge ${r.kind === "zakaz_produktu" ? "error" : r.kind === "wskazowka" ? "" : "ok"}`}>{r.kind_label}</span>
              {!r.active && <span className="badge">wyłączona</span>}</div>
            <div style={{ marginTop: 4 }}>{r.description}</div>
            <div className="muted small">{r.source}{r.created_at ? ` · ${dateTime(r.created_at)}` : ""}</div>
          </div>
          <div className="row">
            <button className="btn small" disabled={busy !== null} onClick={() => void run(`a${r.id}`, () => api.setRuleActive(r.id, !r.active),
              r.active ? "Zasada wyłączona." : "Zasada włączona.")}>{r.active ? "Wyłącz" : "Włącz"}</button>
            <button className="btn small ghost" disabled={busy !== null} onClick={() => {
              if (window.confirm("Usunąć tę zasadę? Nowe plany przestaną jej używać.")) void run(`d${r.id}`, () => api.deleteRule(r.id), "Usunięto zasadę.");
            }}>Usuń</button>
          </div>
        </div>)}
      </div>
      {v.targets.length > 0 && <div className="stack">
        <h3>Widełki po zasadach</h3>
        <div className="table-scroll"><table className="table"><thead><tr><th>Osoba</th><th>Składnik</th><th>W założeniach</th><th>W planach</th></tr></thead>
          <tbody>{v.targets.map(t => <tr key={t.person + t.metric}><td>{t.person}</td><td>{t.metric}</td><td>{t.before}</td><td><b>{t.after}</b></td></tr>)}</tbody></table></div>
        <div className="muted small">Kalorie się nie zmieniają — część energii przechodzi z jednego makroskładnika na drugi. Plik założeń zostaje bez zmian.</div>
      </div>}
      {v.rules.length > 0 && <div className="alert info small">Zasady działają w nowych planach i zamianach. Gotowy plan przelicz w zakładce
        Plan tygodnia (Przelicz), a jeśli dania przestały pasować — użyj naprawy. <button className="btn small" onClick={() => go("tydzien")}>Otwórz plan</button></div>}
      <ManualRule view={v} busy={busy !== null} onAdd={(rule) => run("manual", () => api.addRule(rule), "Dodano zasadę.")} />
    </section>

    <section className="card stack">
      <div className="card-head"><h2><Icon name="wrench" /> Do poprawy w programie <span className="muted">({openNotes.length})</span></h2>
        <button className="btn small" onClick={() => void copyProgramNotes()}><Icon name="copy" />Kopiuj otwarte</button></div>
      <div className="muted small">Uwagi, których nie da się załatwić zasadą diety — np. wygląd eksportu albo listy zakupów. Program ich sam nie wprowadza; przekaż je twórcy.</div>
      {!v.program_notes.length && <div className="muted">Brak uwag do programu.</div>}
      <div className="list">
        {v.program_notes.map(n => <div key={n.id} className="list-item nb-item" style={{ opacity: n.status === "zrobione" ? 0.6 : 1 }}>
          <div className="grow"><div style={{ textDecoration: n.status === "zrobione" ? "line-through" : undefined }}>{n.text}</div>
            <div className="muted small">{dateTime(n.created_at)}{n.status === "zrobione" ? " · zrobione" : ""}</div></div>
          <div className="row">
            <button className="btn small" disabled={busy !== null} onClick={() => void run(`p${n.id}`, () => api.setProgramNote(n.id, n.status === "nowa" ? "zrobione" : "nowa"))}>
              {n.status === "nowa" ? "Zrobione" : "Przywróć"}</button>
            <button className="btn small ghost" disabled={busy !== null} onClick={() => void run(`x${n.id}`, () => api.setProgramNote(n.id, "usunieta"))}>Usuń</button>
          </div>
        </div>)}
      </div>
    </section>

    <Habits view={v} busy={busy} onRun={run} />

    <section className="card stack">
      <div className="card-head"><h2>Historia uwag <span className="muted">({v.notes.length})</span></h2></div>
      {!v.notes.length && <div className="muted">Tu pojawią się Twoje uwagi i to, co z nich wynikło.</div>}
      {v.notes.map(n => <details key={n.id} className="list-item nb-item" style={{ display: "block" }}>
        <summary><b>{dateTime(n.created_at)}</b>{n.channel !== "aplikacja" ? ` · ${n.channel}` : ""} — {n.text.length > 140 ? `${n.text.slice(0, 140)}…` : n.text}</summary>
        <div className="stack" style={{ marginTop: 8 }}>
          <div style={{ whiteSpace: "pre-wrap" }}>{n.text}</div>
          {n.analysis && <AnalysisBox analysis={n.analysis} />}
        </div>
      </details>)}
    </section>
  </div>;
}

function AnalysisBox({ analysis }: { analysis: NoteAnalysis }) {
  const a = analysis;
  return <div className="alert ok stack" style={{ gap: 6 }}>
    {a.summary && <div><b>Zrozumiałem:</b> {a.summary}</div>}
    {!!a.rules_added?.length && <div><b>Nowe zasady:</b><ul>{a.rules_added.map(r => <li key={r.id}>{r.description}</li>)}</ul></div>}
    {!!a.rules_kept?.length && <div><b>Bez zmian (już zapisane):</b><ul>{a.rules_kept.map(r => <li key={r}>{r}</li>)}</ul></div>}
    {!!a.rules_removed?.length && <div><b>Usunięte lub zastąpione:</b><ul>{a.rules_removed.map(r => <li key={r}>{r}</li>)}</ul></div>}
    {!!a.ratings?.length && <div><b>Oceny dań:</b> {a.ratings.map(r => `${r.rating > 0 ? "👍" : "👎"} ${r.name}`).join(", ")}</div>}
    {!!a.program_notes?.length && <div><b>Do poprawy w programie:</b><ul>{a.program_notes.map(x => <li key={x}>{x}</li>)}</ul></div>}
    {!!a.problems?.length && <div className="bad-text"><b>Uwagi:</b><ul>{a.problems.map(x => <li key={x}>{x}</li>)}</ul></div>}
    {!a.summary && !a.rules_added?.length && !a.problems?.length && <div>Zapisano notatkę.</div>}
  </div>;
}

function Habits({ view, busy, onRun }: { view: NotebookView; busy: string | null;
  onRun: (key: string, action: () => Promise<NotebookView>, done?: string) => Promise<void> }) {
  const h = view.habits;
  return <section className="card stack">
    <div className="card-head"><h2><Icon name="brain" /> Twoje nawyki</h2>
      <button className="btn" disabled={busy !== null} onClick={() => void onRun("habits", () => api.analyzeHabits())}>
        {busy === "habits" ? "Analizuję…" : h ? "Przeanalizuj ponownie" : "Przeanalizuj nawyki"}</button></div>
    <div className="muted small">Program zbiera oceny 👍/👎, wymiany dań w planach, Twoje uwagi i życzenia. Model opisuje wzorce i proponuje
      zasady — dodajesz tylko te, które chcesz.</div>
    {busy === "habits" && <Spinner label="Zbieram sygnały i szukam wzorców…" />}
    {h && <div className="stack">
      <div className="chips">{h.stats.map(s => <span key={s} className="chip">{s}</span>)}</div>
      {h.problems.length > 0 && <div className="alert warn small">{h.problems.join(" ")}</div>}
      {h.observations.length > 0 && <div><b>Wnioski</b> <span className="muted small">({dateTime(h.created_at)})</span>
        <ul>{h.observations.map(o => <li key={o}>{o}</li>)}</ul></div>}
      {h.proposals.length > 0 && <div className="stack"><b>Propozycje zasad</b>
        {h.proposals.map(p => <div key={p.id} className="list-item nb-item"><span className="grow">{p.description}</span>
          {p.applied ? <span className="badge ok">dodano</span> :
            <button className="btn small primary" disabled={busy !== null} onClick={() => void onRun(`h${p.id}`, () => api.applyProposal(p.id), "Dodano zasadę.")}>Zastosuj</button>}
        </div>)}</div>}
      {h.program_notes.length > 0 && <div className="stack"><b>Możliwe poprawki w programie</b>
        {h.program_notes.map((x, i) => <div key={x} className="list-item nb-item"><span className="grow">{x}</span>
          <button className="btn small" disabled={busy !== null} onClick={() => void onRun(`pn${i}`, () => api.programNoteFromHabits(i), "Dodano do listy do poprawy.")}>Dodaj do listy</button></div>)}
      </div>}
    </div>}
  </section>;
}

const KIND_OPTIONS: { id: RuleKind; label: string }[] = [
  { id: "limit_porcji", label: "Limit porcji (np. ryż)" },
  { id: "zakaz_produktu", label: "Zakaz produktu" },
  { id: "zamiana_makro", label: "Mniej jednego makroskładnika, więcej drugiego" },
  { id: "wskazowka", label: "Wskazówka do układania" },
];

function ManualRule({ view, busy, onAdd }: { view: NotebookView; busy: boolean; onAdd: (rule: RuleInput) => Promise<void> }) {
  const [kind, setKind] = useState<RuleKind>("limit_porcji");
  const [target, setTarget] = useState<"kategoria" | "produkt">("kategoria");
  const [category, setCategory] = useState("ryz");
  const [product, setProduct] = useState<ProductListItem | null>(null);
  const [persons, setPersons] = useState<string[]>([]);
  const [meals, setMeals] = useState<string[]>([]);
  const [grams, setGrams] = useState<Record<string, string>>({});
  const [from, setFrom] = useState("weglowodany");
  const [to, setTo] = useState("bialko");
  const [hint, setHint] = useState("");
  const toggle = (list: string[], id: string) => list.includes(id) ? list.filter(x => x !== id) : [...list, id];

  const submit = async () => {
    const rule: RuleInput = { typ: kind, osoby: persons, posilki: meals };
    if (kind === "zakaz_produktu" || kind === "limit_porcji") {
      if (target === "produkt") { if (!product) return; rule.produkt = product.id; }
      else rule.kategoria = category;
    }
    if (kind === "limit_porcji") {
      rule.gramy = Object.fromEntries(Object.entries(grams).filter(([, g]) => g.trim()).map(([p, g]) => [p, Number(g.replace(",", "."))]));
    }
    if (kind === "zamiana_makro") { rule.z = from; rule.na = to; }
    if (kind === "wskazowka") { if (!hint.trim()) return; rule.tekst = hint.trim(); }
    await onAdd(rule);
    setGrams({}); setHint(""); setProduct(null);
  };

  return <details className="list-item nb-item" style={{ display: "block" }}>
    <summary><b>Dodaj zasadę ręcznie</b> <span className="muted small">— działa także bez klucza AI</span></summary>
    <div className="stack" style={{ marginTop: 10 }}>
      <label className="field"><span>Rodzaj</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as RuleKind)}>
          {KIND_OPTIONS.map(k => <option key={k.id} value={k.id}>{k.label}</option>)}</select></label>
      {(kind === "zakaz_produktu" || kind === "limit_porcji") && <div className="stack">
        <div className="row">
          <label className="check"><input type="radio" checked={target === "kategoria"} onChange={() => setTarget("kategoria")} /> Cała kategoria</label>
          <label className="check"><input type="radio" checked={target === "produkt"} onChange={() => setTarget("produkt")} /> Jeden produkt</label>
        </div>
        {target === "kategoria" ? <label className="field"><span>Kategoria</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {view.categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          : <ProductPicker value={product} onChange={setProduct} />}
      </div>}
      {kind === "limit_porcji" && <div className="row">
        {view.persons.map(p => <label key={p.id} className="field"><span>{p.name}: maks. g na posiłek</span>
          <input inputMode="decimal" placeholder="policzy program" value={grams[p.id] ?? ""}
            onChange={(e) => setGrams({ ...grams, [p.id]: e.target.value })} /></label>)}
      </div>}
      {kind === "zamiana_makro" && <div className="row">
        <label className="field"><span>Mniej</span><select value={from} onChange={(e) => setFrom(e.target.value)}>
          {view.macros.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
        <label className="field"><span>Więcej</span><select value={to} onChange={(e) => setTo(e.target.value)}>
          {view.macros.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
        <span className="muted small">Ile gramów — policzy program (ok. 4% energii dnia, kalorie bez zmian).</span>
      </div>}
      {kind === "wskazowka" && <label className="field"><span>Wskazówka</span>
        <input maxLength={300} value={hint} placeholder="np. mniej dań z jajkami na kolację" onChange={(e) => setHint(e.target.value)} /></label>}
      {kind !== "wskazowka" && <div className="row"><span className="muted small">Dotyczy:</span>
        {view.persons.map(p => <label key={p.id} className="check"><input type="checkbox" checked={persons.includes(p.id)}
          onChange={() => setPersons(toggle(persons, p.id))} /> {p.name}</label>)}
        <span className="muted small">(nic nie zaznaczone = wszyscy)</span></div>}
      {kind === "limit_porcji" && <div className="row"><span className="muted small">Posiłki:</span>
        {view.meals.map(m => <label key={m.id} className="check"><input type="checkbox" checked={meals.includes(m.id)}
          onChange={() => setMeals(toggle(meals, m.id))} /> {m.name}</label>)}
        <span className="muted small">(nic = każdy)</span></div>}
      <div><button className="btn primary" disabled={busy || (kind === "wskazowka" && !hint.trim())
        || ((kind === "zakaz_produktu" || kind === "limit_porcji") && target === "produkt" && !product)} onClick={() => void submit()}>Dodaj zasadę</button></div>
    </div>
  </details>;
}

function ProductPicker({ value, onChange }: { value: ProductListItem | null; onChange: (p: ProductListItem | null) => void }) {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ProductListItem[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) { setItems([]); return; }
    const t = setTimeout(() => { api.products({ q }).then(r => setItems(r.slice(0, 8))).catch(() => setItems([])); }, 250);
    return () => clearTimeout(t);
  }, [q]);
  if (value) return <div className="row"><span className="chip">{value.name}</span>
    <button className="btn small ghost" onClick={() => onChange(null)}>Zmień</button></div>;
  return <div className="stack" style={{ gap: 6 }}>
    <label className="field"><span>Produkt</span><input type="search" value={q} placeholder="Szukaj produktu…" onChange={(e) => setQ(e.target.value)} /></label>
    {items.length > 0 && <div className="chips">{items.map(p => <button key={p.id} className="btn small" onClick={() => { onChange(p); setQ(""); }}>{p.name}</button>)}</div>}
  </div>;
}
