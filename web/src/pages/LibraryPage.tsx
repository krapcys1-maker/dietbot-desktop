import { useState } from "react";
import { api } from "../api";
import SaveDietButton from "../components/SaveDietButton";
import { ErrorBox, Loading, useToast } from "../components/ui";
import { dateTime, go, useAsync } from "../util";

export default function LibraryPage() {
  const toast = useToast();
  const list = useAsync(() => api.library(), []);
  const [history, setHistory] = useState(false);
  const [allVersions, setAllVersions] = useState(false);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const latest = new Map<string, number>();
  for (const p of list.data ?? []) latest.set(p.family_id, Math.max(latest.get(p.family_id) ?? 0, p.version));
  const items = (list.data ?? []).filter((p) => (history || p.saved_diet)
    && (!history || allVersions || p.saved_diet || p.active || p.version === latest.get(p.family_id))
    && `${p.saved_diet?.title ?? ""} ${p.title} ${p.wishes ?? ""}`.toLocaleLowerCase("pl").includes(query.toLocaleLowerCase("pl")))
    .sort((a, b) => (b.saved_diet?.saved_at ?? b.created_at).localeCompare(a.saved_diet?.saved_at ?? a.created_at));
  const unsave = async (id: string) => {
    if (!window.confirm("Usunąć z zapisanych diet? Plan pozostanie w historii.")) return;
    setBusy(id);
    try { await api.unsaveDiet(id); list.reload(); toast("Usunięto z zapisanych. Plan nadal jest w historii."); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(null); }
  };
  return <div className="page stack">
    <div className="page-head"><div>
      <span className="eyebrow">DO DOBRYCH TYGODNI WARTO WRACAĆ</span><h1>Moje diety</h1>
      <div className="sub">Zapisane tygodnie i historia planów. Otwarcie diety nie zmienia jej dań ani porcji.</div>
    </div><button className="btn primary" onClick={() => go("nowy")}>Ułóż nowy plan</button></div>
    <div className="row filters">
      <button className={`btn ${!history ? "primary" : ""}`} onClick={() => setHistory(false)}>Zapisane diety</button>
      <button className={`btn ${history ? "primary" : ""}`} onClick={() => setHistory(true)}>Historia wszystkich planów</button>
      <input type="search" aria-label="Szukaj diety" placeholder="Szukaj diety…" value={query} onChange={(e) => setQuery(e.target.value)} />
      {history && <label className="check"><input type="checkbox" checked={allVersions} onChange={(e) => setAllVersions(e.target.checked)} /> Pokaż również starsze wersje</label>}
    </div>
    {list.loading && !list.data && <Loading />}
    {list.error && <ErrorBox error={list.error} onRetry={list.reload} />}
    {list.data && !items.length && <div className="card empty">
      {query ? "Nie znaleziono pasujących diet." : history ? "Nie ma jeszcze planów." : "Nie masz jeszcze zapisanych diet. Otwórz Tydzień i kliknij „Zapisz dietę” albo wybierz plan z historii."}
    </div>}
    {items.map((p) => <section className="card stack" key={p.id}>
      <div><div className="row"><h2>{p.saved_diet?.title ?? p.title}</h2>
        {p.active && <span className="badge ok">aktualny</span>}
        {p.saved_diet && <span className="badge">zapisana dieta</span>}</div>
        <div className="sub">{p.title} · wersja {p.version} · utworzono {dateTime(p.created_at)}</div>
        {p.change_note && <div className="sub">{p.change_note}</div>}
      </div>
      <div className="row">
        <button className="btn primary" onClick={() => go("tydzien", p.id)}>Otwórz dietę</button>
        <button className="btn" onClick={() => go("zakupy", p.id)}>Lista zakupów</button>
        <SaveDietButton plan={p} onSaved={list.reload} />
        {p.saved_diet && <button className="btn" disabled={busy !== null} onClick={() => void unsave(p.id)}>Usuń z zapisanych</button>}
      </div>
    </section>)}
  </div>;
}
