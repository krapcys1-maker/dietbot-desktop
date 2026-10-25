import { useState } from "react";
import { api } from "../api";
import Icon from "./Icon";
import { ErrorBox, JobProgress, Loading, useToast } from "./ui";
import type { Job, TasteProposal, TasteView } from "../types";
import { dateTime, go, useAsync } from "../util";

/** Propozycje dań w guście domowników: program zbiera sygnały, model wybiera z biblioteki i podsuwa pomysły,
 *  domownicy decydują, co trafi do następnego planu. */
export default function TasteProposals() {
  const toast = useToast();
  const t = useAsync<TasteView>(() => api.taste(), []);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const start = async (key: string, action: () => Promise<Job>) => {
    if (busy || jobId) return;
    setBusy(key);
    try { setJobId((await action()).id); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(null); }
  };
  const decide = async (key: string, action: () => Promise<TasteView>, done?: string) => {
    if (busy) return;
    setBusy(key);
    try { t.setData(await action()); if (done) toast(done); }
    catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(null); }
  };
  const finished = (job: Job) => {
    setJobId(null);
    t.reload();
    if (job.status === "gotowe" && job.result?.name) toast(`Nowe danie: ${job.result.name} — trafi do następnego planu.`);
  };

  if (t.loading && !t.data) return <section className="card"><Loading /></section>;
  if (t.error && !t.data) return <section className="card"><ErrorBox error={t.error} onRetry={t.reload} /></section>;
  const v = t.data!;
  const visible = v.propozycje.filter((p) => p.status !== "odrzucona");
  const ideas = v.pomysly.map((idea, i) => ({ idea, i })).filter(({ idea }) => idea.status !== "odrzucony");

  return <section className="card stack">
    <div className="card-head"><h2><Icon name="sparkles" /> Propozycje dla Was</h2>
      <button className="btn primary" disabled={!v.llm_available || busy !== null || jobId !== null}
        onClick={() => void start("refresh", () => api.tasteRefresh())}>
        {v.utworzono ? "Odśwież propozycje" : "Zaproponuj dania"}</button></div>
    <div className="muted small">Porcja uczy się z Waszych ocen 👍/👎, zamian dań w planach, uwag i próśb w czacie.
      Model wybiera dania z biblioteki, których jeszcze nie jedliście, i podsuwa pomysły na nowe (zwykle mniej niż cent).
      Wybrane dania trafią do następnego planu — gramatury jak zawsze liczy program.</div>
    {!v.llm_available && <div className="alert warn small">Propozycje przygotowuje model językowy — dodaj klucz DeepSeek
      w <button className="btn small" onClick={() => go("ustawienia")}>Ustawieniach</button>.</div>}
    {jobId && <JobProgress jobId={jobId} onDone={finished} />}
    {v.do_planu.length > 0 && <div className="alert ok small">
      <b>Do następnego planu:</b> {v.do_planu.join(", ")}.{" "}
      <button className="btn small" onClick={() => go("nowy")}>Ułóż plan</button></div>}
    {v.podsumowanie && <div><b>Czego się o Was nauczyłem:</b> {v.podsumowanie}{" "}
      {v.utworzono && <span className="muted small">({dateTime(v.utworzono)})</span>}</div>}
    {(v.lubia.length > 0 || v.nie_lubia.length > 0) && <div className="chips">
      {v.lubia.map((x) => <span key={`+${x}`} className="badge ok">lubicie: {x}</span>)}
      {v.nie_lubia.map((x) => <span key={`-${x}`} className="badge warn">raczej nie: {x}</span>)}
    </div>}
    {visible.length > 0 && <div className="list">
      {visible.map((p) => <ProposalRow key={p.danie} p={p} busy={busy !== null}
        onChoose={(status, done) => void decide(`${status}${p.danie}`, () => api.tasteChoose(p.danie, status), done)} />)}
    </div>}
    {ideas.length > 0 && <div className="stack"><b>Pomysły na nowe dania</b>
      {ideas.map(({ idea, i }) => <div key={idea.opis} className="list-item nb-item">
        <div className="grow">
          <div><b>{idea.opis}</b> <span className="muted small">— {idea.posilek_nazwa.toLowerCase()}, kuchnia {idea.kuchnia_nazwa}</span></div>
          {idea.powod && <div className="muted small">{idea.powod}</div>}
          {idea.status === "nieudany" && <div className="warn-text small">Przepis nie przeszedł kontroli — możesz spróbować jeszcze raz.</div>}
        </div>
        {idea.status === "wymyslony"
          ? <span className="badge ok">w bibliotece{idea.danie_nazwa ? `: ${idea.danie_nazwa}` : ""}</span>
          : <div className="row">
            <button className="btn small primary" disabled={!v.llm_available || busy !== null || jobId !== null}
              onClick={() => void start(`i${i}`, () => api.tasteInvent(i))}>Wymyśl to danie</button>
            <button className="btn small ghost" disabled={busy !== null}
              onClick={() => void decide(`x${i}`, () => api.tasteDismissIdea(i))}>Nie</button>
          </div>}
      </div>)}
      <div className="muted small">Wymyślone danie przechodzi kontrolę programu, silnika i kucharza, trafia do biblioteki
        i do następnego planu (kilka centów).</div>
    </div>}
    {!v.utworzono && !jobId && v.llm_available && <div className="muted">Kliknij „Zaproponuj dania” — im więcej ocen i zamian,
      tym trafniejsze propozycje.</div>}
  </section>;
}

function ProposalRow({ p, busy, onChoose }: { p: TasteProposal; busy: boolean;
  onChoose: (status: "nowa" | "wybrana" | "odrzucona", done?: string) => void }) {
  return <div className="list-item nb-item" style={{ opacity: p.status === "w_planie" ? 0.7 : 1 }}>
    <div className="grow">
      <div><b>{p.nazwa}</b> {p.nowe && <span className="badge ok">nowe</span>}{" "}
        <span className="muted small">— {p.posilki.join(", ").toLowerCase()}, kuchnia {p.kuchnia}</span></div>
      {p.powod && <div className="muted small">{p.powod}</div>}
    </div>
    {p.status === "nowa" && <div className="row">
      <button className="btn small primary" disabled={busy} onClick={() => onChoose("wybrana", "Dodano do następnego planu.")}>
        Chcę w następnym planie</button>
      <button className="btn small ghost" disabled={busy} onClick={() => onChoose("odrzucona")}>Nie teraz</button>
    </div>}
    {p.status === "wybrana" && <div className="row"><span className="badge ok">do następnego planu</span>
      <button className="btn small ghost" disabled={busy} onClick={() => onChoose("nowa")}>Cofnij</button></div>}
    {p.status === "w_planie" && <span className="badge">już w planie</span>}
  </div>;
}
