import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorBox, Loading, Modal, Spinner, useToast } from "../components/ui";
import type { AssumptionsResponse, ProductDetail, ProductListItem } from "../types";
import { go, num, plural, STATE_NAMES, useAsync } from "../util";

const SOURCE_NAMES: Record<string, string> = {
  BLS: "BLS 4.0 (Niemcy)", CIQUAL: "CIQUAL (Francja)", OFF: "Open Food Facts", etykieta: "etykieta", USDA: "USDA", recznie: "wpis ręczny",
};

export default function ProductsPage({ productId }: { productId?: string }) {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setSearch(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);
  const [category, setCategory] = useState("");
  const [adding, setAdding] = useState(false);
  const meta = useAsync<AssumptionsResponse>(() => api.assumptions(), []);
  const list = useAsync<ProductListItem[]>(() => api.products({ q: search || undefined, kategoria: category || undefined }), [search, category]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Produkty</h1>
          <div className="sub">Wartości na 100 g części jadalnej. Główne źródło: BLS 4.0 (Max Rubner-Institut, CC BY 4.0) i etykiety produktów.</div>
        </div>
        <button className="btn primary" onClick={() => setAdding(true)}>+ Dodaj z etykiety</button>
      </div>
      <div className="row">
        <input type="search" placeholder="Szukaj produktu…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 260 }} />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Wszystkie kategorie</option>
          {meta.data?.categories.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        {list.data && <span className="muted">{list.data.length} {plural(list.data.length, "produkt", "produkty", "produktów")}</span>}
      </div>
      {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
      <div className="split">
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          {list.loading && !list.data && <Loading />}
          <table className="table">
            <thead>
              <tr><th>Produkt</th><th className="right">kcal</th><th className="right">B</th><th className="right">T</th><th className="right">W</th></tr>
            </thead>
            <tbody>
              {list.data?.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => go("produkty", p.id)} style={productId === p.id ? { background: "var(--primary-soft)" } : undefined}>
                  <td>
                    {p.name}
                    <div className="muted small">{p.category_label} · {SOURCE_NAMES[p.source] ?? p.source}</div>
                  </td>
                  <td className="right">{num(p.per_100g.kcal)}</td>
                  <td className="right">{num(p.per_100g.protein, 1)}</td>
                  <td className="right">{num(p.per_100g.fat, 1)}</td>
                  <td className="right">{num(p.per_100g.carbs, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="sticky">{productId ? <ProductCard id={productId} /> : <div className="card empty">Wybierz produkt z listy.</div>}</div>
      </div>
      {adding && meta.data && (
        <AddProductDialog
          categories={meta.data.categories}
          onClose={() => setAdding(false)}
          onAdded={(p) => {
            setAdding(false);
            list.reload();
            go("produkty", p.id);
          }}
        />
      )}
    </div>
  );
}

function ProductCard({ id }: { id: string }) {
  const { data: p, error, loading } = useAsync<ProductDetail>(() => api.product(id), [id]);
  if (loading && !p) return <div className="card"><Loading /></div>;
  if (error) return <ErrorBox error={error} />;
  if (!p) return null;
  const n = p.per_100g;
  return (
    <section className="card stack">
      <div>
        <h2>{p.name}</h2>
        <div className="muted small">{p.category_label} · stan: {STATE_NAMES[p.state] ?? p.state.replaceAll("_", " ")}</div>
      </div>
      {p.aliases.length > 0 && <div className="small"><b>Inne nazwy:</b> {p.aliases.join(", ")}</div>}
      <table className="table">
        <tbody>
          <tr><td>Energia</td><td className="right"><b>{num(n.kcal)} kcal</b></td></tr>
          <tr><td>Białko</td><td className="right">{num(n.protein, 1)} g</td></tr>
          <tr><td>Tłuszcz</td><td className="right">{num(n.fat, 1)} g</td></tr>
          <tr><td>Węglowodany</td><td className="right">{num(n.carbs, 1)} g</td></tr>
          {n.sugars !== null && <tr><td className="muted">w tym cukry</td><td className="right muted">{num(n.sugars, 1)} g</td></tr>}
          {n.fiber !== null && <tr><td>Błonnik</td><td className="right">{num(n.fiber, 1)} g</td></tr>}
          {n.salt !== null && <tr><td>Sól</td><td className="right">{num(n.salt, 2)} g</td></tr>}
        </tbody>
      </table>
      <div className="small">
        <b>Źródło:</b> {SOURCE_NAMES[p.source.baza] ?? p.source.baza}
        {p.source.kod ? ` · kod ${p.source.kod}` : ""}
        {p.source.nazwa_oryginalna ? <div className="muted">{p.source.nazwa_oryginalna}</div> : null}
        {p.source.uwagi ? <div className="muted">{p.source.uwagi}</div> : null}
      </div>
      {p.measures.length > 0 && (
        <div className="small">
          <b>Miary domowe:</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {p.measures.map((m, i) => (
              <li key={i}>{m.description ?? m.unit} = {num(m.grams, 1)} g {m.source && <span className="muted">({m.source})</span>}</li>
            ))}
          </ul>
        </div>
      )}
      {p.stores.length > 0 && (
        <div className="small">
          <b>W sklepie:</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {p.stores.map((s, i) => (
              <li key={i}>
                {String(s.sklep)}: <i>{String(s.nazwa_w_sklepie)}</i>{s.marka ? `, ${String(s.marka)}` : ""}{s.opakowanie_g ? ` — ${num(Number(s.opakowanie_g))} g` : ""}
                {s.preferowany ? " ✓" : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {p.notes && <div className="alert info small">{p.notes}</div>}
      {p.used_in.length > 0 && (
        <div className="small">
          <b>W daniach ({p.used_in.length}):</b> {p.used_in.map((d, i) => (
            <span key={d.id}>{i > 0 && ", "}<a href={`#/dania/${d.id}`}>{d.name}</a></span>
          ))}
        </div>
      )}
      <div className="muted small">Krok gramatury: {num(p.step_g)} g{p.max_portion_g ? ` · maks. porcja ${num(p.max_portion_g)} g` : ""}{p.density_g_per_ml ? ` · gęstość ${num(p.density_g_per_ml, 3)} g/ml` : ""}</div>
    </section>
  );
}

function AddProductDialog({ categories, onClose, onAdded }: { categories: { id: string; name: string }[]; onClose: () => void; onAdded: (p: ProductDetail) => void }) {
  const toast = useToast();
  const [form, setForm] = useState<Record<string, string>>({ category: "nabial", state: "gotowy_do_jedzenia", store: "Lidl RO" });
  const [confirmMismatch, setConfirmMismatch] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const numVal = (k: string) => (form[k] ? Number(form[k].replace(",", ".")) : undefined);

  const kcalCheck = () => {
    const p = numVal("protein"), f = numVal("fat"), c = numVal("carbs"), k = numVal("kcal");
    if ([p, f, c, k].some((x) => x === undefined || Number.isNaN(x))) return null;
    const computed = 4 * p! + 9 * f! + 4 * c! + 2 * (numVal("fiber") ?? 0);
    return { computed, diff: Math.abs(computed - k!) / Math.max(computed, k!, 1) };
  };
  const check = kcalCheck();

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        name: form.name, aliases: form.aliases, category: form.category, state: form.state,
        dietary_class: form.dietary_class || "nieznany",
        kcal: numVal("kcal"), protein: numVal("protein"), fat: numVal("fat"), carbs: numVal("carbs"),
        fiber: numVal("fiber"), sugars: numVal("sugars"), salt: numVal("salt"), saturated_fat: numVal("saturated_fat"),
        ean: form.ean, store: form.store, brand: form.brand, store_name_local: form.store_name_local, package_g: numVal("package_g"),
        notes: form.notes, confirm_mismatch: confirmMismatch,
      };
      const p = await api.addProduct(payload);
      toast(`Dodano: ${p.name}`);
      onAdded(p);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = (k: string, label: string, props: Record<string, unknown> = {}) => (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={form[k] ?? ""} onChange={set(k)} {...props} />
    </label>
  );

  return (
    <Modal
      title="Nowy produkt z etykiety"
      onClose={onClose}
      confirmClose={() => !Object.entries(form).some(([k, v]) => !["category", "state", "store"].includes(k) && String(v ?? "").trim())
        || window.confirm("Zamknąć bez zapisywania produktu? Wpisane wartości przepadną.")}
      wide
      footer={
        <>
          <button className="btn" onClick={onClose}>Anuluj</button>
          <button className="btn primary" onClick={submit} disabled={busy || !form.name}>{busy ? <Spinner /> : "Zapisz produkt"}</button>
        </>
      }
    >
      <div className="muted small">Przepisz wartości „w 100 g” z opakowania. Program sprawdzi, czy kalorie zgadzają się z makroskładnikami.</div>
      <label className="field"><span>Zgodność diety — sprawdź pełny skład na etykiecie</span>
        <select value={form.dietary_class || "nieznany"} onChange={set("dietary_class")}>
          <option value="nieznany">Nie wiem — pomijaj w trybach roślinnych</option><option value="wegan">Wegański — bez składników odzwierzęcych</option>
          <option value="wege">Wegetariański — może zawierać nabiał / jaja</option><option value="zwierzecy">Z mięsem, rybą lub innymi składnikami niewegetariańskimi</option>
        </select>
      </label>
      <p className="muted small">Napój roślinny dodaj w kategorii Napoje, nie Nabiał. Deklaracja nie zastępuje kontroli składu, żelatyny i podpuszczki.</p>
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 1fr", gap: 10 }}>
        {field("name", "Nazwa (po polsku)", { placeholder: "np. Twaróg chudy (brânză de vaci 0,5%)" })}
        <label className="field">
          <span>Kategoria</span>
          <select value={form.category} onChange={set("category")}>
            {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Stan</span>
          <select value={form.state} onChange={set("state")}>
            <option value="gotowy_do_jedzenia">gotowy do jedzenia</option>
            <option value="surowy">surowy</option>
            <option value="suchy">suchy (przed gotowaniem)</option>
            <option value="gotowany">gotowany</option>
            <option value="odsaczony">odsączony</option>
          </select>
        </label>
      </div>
      {field("aliases", "Inne nazwy (po przecinku)", { placeholder: "twaróg chudy, ser biały chudy" })}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
        {field("kcal", "Energia [kcal]")}
        {field("protein", "Białko [g]")}
        {field("fat", "Tłuszcz [g]")}
        {field("carbs", "Węglowodany [g]")}
        {field("sugars", "w tym cukry [g]")}
        {field("saturated_fat", "kwasy nasycone [g]")}
        {field("fiber", "Błonnik [g]")}
        {field("salt", "Sól [g]")}
      </div>
      {check && (
        <div className={`alert ${check.diff > 0.08 ? "warn" : "ok"} small`}>
          Z makro wychodzi ok. {num(check.computed)} kcal — {check.diff > 0.08 ? `różnica ${num(check.diff * 100)}%, sprawdź przepisanie.` : "zgadza się z etykietą."}
          {check.diff > 0.08 && (
            <label className="check" style={{ marginLeft: 10 }}>
              <input type="checkbox" checked={confirmMismatch} onChange={(e) => setConfirmMismatch(e.target.checked)} /> wartości są zgodne z etykietą
            </label>
          )}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
        {field("store", "Sklep", { placeholder: "Lidl RO" })}
        {field("store_name_local", "Nazwa na półce", { placeholder: "Brânză de vaci" })}
        {field("brand", "Marka", { placeholder: "Pilos" })}
        {field("package_g", "Opakowanie [g]")}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 10 }}>
        {field("ean", "Kod EAN (opcjonalnie)")}
        {field("notes", "Uwagi (opcjonalnie)")}
      </div>
      {error && <div className="alert error">{error}</div>}
    </Modal>
  );
}
