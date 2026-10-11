import { api } from "../api";
import ExportButtons from "../components/ExportButtons";
import Icon from "../components/Icon";
import PlateArt from "../components/PlateArt";
import { ErrorBox, Loading } from "../components/ui";
import type { PlanView, Status } from "../types";
import { plural, useAsync } from "../util";

export default function HomePage({ status }: { status: Status | null }) {
  const plan = useAsync<PlanView | null>(() => api.activePlan().catch(e => e.status === 404 ? null : Promise.reject(e)), []);
  const current = plan.data;
  return <div className="page home-page">
    <section className="welcome-hero">
      <div className="hero-copy">
        <span className="eyebrow"><span className="live-dot" /> MNIEJ PLANOWANIA. WIĘCEJ SMAKU.</span>
        <h1>Dobry tydzień<br />zaczyna się <em>przy stole.</em></h1>
        <p>Twoje ulubione dania, dopasowane porcje i jedna lista zakupów. Wszystko w swoim miejscu.</p>
        <div className="row">
          <a className="btn primary large" href={current ? `#/tydzien/${current.id}` : "#/nowy"}>{current ? "Otwórz mój plan" : "Ułóż pierwszy plan"}<Icon name="arrow" /></a>
          <a className="hero-link" href={current ? "#/nowy" : "#/zalozenia"}>{current ? "Zaplanuj kolejny tydzień" : "Najpierw dopasuj cele"}</a>
        </div>
      </div>
      <div className="hero-art"><PlateArt /><span className="art-note"><Icon name="leaf" /> Po Twojemu. Także wege i wegan.</span></div>
    </section>
    {plan.loading && <Loading />}
    {plan.error && <ErrorBox error={plan.error} onRetry={plan.reload} />}
    {current && <section className="card current-plan">
      <div className="current-plan-icon"><Icon name="calendar" /></div>
      <div className="grow"><span className="eyebrow">TWÓJ AKTUALNY PLAN</span><h2>{current.title}</h2>
        <p className="muted">{current.days.length} {plural(current.days.length, "dzień", "dni", "dni")} · {current.persons.map(p => p.name).join(" i ")} · {current.ok ? "Sprawdzony według zapisanych założeń" : "Ma uwagi — sprawdź je w planie"}</p></div>
      <ExportButtons planId={current.id} />
    </section>}
    <div className="section-heading"><div><span className="eyebrow">TWOJA KUCHNIA, TWÓJ RYTM</span><h2>{current ? "Wszystko pod ręką" : "Zacznij od trzech prostych kroków"}</h2></div><a href="#/pomoc" className="text-link">Jak to działa <Icon name="arrow" /></a></div>
    <div className="feature-grid">
      <a className="feature-card" href="#/zalozenia"><span className="feature-icon sage"><Icon name="target" /></span><span className="feature-index">01</span><h3>Dopasuj do siebie</h3><p>Wybierz sposób jedzenia i cele. Kalkulator kalorii jest opcjonalny.</p><span className="text-link">Cele i preferencje <Icon name="arrow" /></span></a>
      <a className="feature-card" href="#/dania"><span className="feature-icon peach"><Icon name="dish" /></span><span className="feature-index">02</span><h3>Znajdź swoje smaki</h3><p>{status ? `${status.approved_dishes} zatwierdzonych dań. ` : "Biblioteka dań. "}Oceniaj przepisy, żeby kolejne plany lepiej Ci pasowały.</p><span className="text-link">Przeglądaj dania <Icon name="arrow" /></span></a>
      <a className="feature-card" href={current ? `#/zakupy/${current.id}` : "#/nowy"}><span className="feature-icon butter"><Icon name="basket" /></span><span className="feature-index">03</span><h3>Zakupy bez chaosu</h3><p>Jedna lista na cały plan, połączone produkty i wygodne odhaczanie.</p><span className="text-link">{current ? "Otwórz zakupy" : "Ułóż plan i listę"} <Icon name="arrow" /></span></a>
    </div>
    <section className="local-note"><Icon name="shield" /><div><b>Bezpłatna aplikacja. Twoje dane na Twoim komputerze.</b><p>Podstawowe planowanie działa bez konta i bez AI. Opcjonalne AI używa Twojego klucza DeepSeek — koszty jego użycia rozliczasz u dostawcy.</p></div><a href="#/ustawienia" className="text-link">{status?.llm.key_present ? "Ustawienia AI" : "Dodaj klucz AI"}<Icon name="arrow" /></a></section>
  </div>;
}
