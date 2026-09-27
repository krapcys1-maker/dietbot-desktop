import { useState } from "react";
import { api } from "../api";
import type { PersonData } from "../types";

export interface GoalEstimate {
  resting_kcal: number; maintenance_kcal: number; deficit_kcal: number; loss_kg: number;
  targets: PersonData["cele_dzienne"]; protein_anchor: PersonData["kotwica_bialka_na_posilek"];
  method: string; warnings: string[]; sources: { label: string; url: string }[];
}

export default function GoalCalculator({ meals, onApply }: { meals: number; onApply: (r: GoalEstimate) => void }) {
  const [age, setAge] = useState("");
  const [height, setHeight] = useState("");
  const [weight, setWeight] = useState("");
  const [sex, setSex] = useState("female");
  const [activity, setActivity] = useState("low");
  const [goal, setGoal] = useState("maintain");
  const [loss, setLoss] = useState("");
  const [pace, setPace] = useState("gentle");
  const [eligible, setEligible] = useState(false);
  const [result, setResult] = useState<GoalEstimate | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const invalidate = () => { setResult(null); setError(""); };
  const calculate = async () => {
    setBusy(true); setResult(null); setError("");
    try {
      if (!age || !height || !weight || (goal === "lose" && (!loss || Number(loss) <= 0))) throw new Error("Uzupełnij parametry; liczba kilogramów do utraty musi być dodatnia.");
      setResult(await api.calculateGoals({ age: Number(age), height_cm: Number(height), weight_kg: Number(weight), sex, activity, goal, pace,
        target_weight_kg: goal === "lose" ? Number(weight) - Number(loss) : null, adult_without_special_needs: eligible, meals }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <details className="disclosure"><summary>Nie znasz swoich celów? Oblicz orientacyjne kcal i makro (opcjonalnie)</summary>
    <div className="stack">
      <p className="muted">Dla osób 18–75 lat. Dane są przeliczane lokalnie, bez AI i bez zapisywania pomiarów. Możesz pominąć kalkulator i wpisać własne cele.</p>
      <fieldset disabled={busy} style={{ border: 0, padding: 0 }} onChange={invalidate}>
        <div className="row">
          <label className="field"><span>Wiek (lata)</span><input type="number" min={18} max={75} value={age} onChange={e => setAge(e.target.value)} /></label>
          <label className="field"><span>Wzrost (cm)</span><input type="number" min={130} max={220} value={height} onChange={e => setHeight(e.target.value)} /></label>
          <label className="field"><span>Masa ciała (kg)</span><input type="number" min={40} max={200} step="0.1" value={weight} onChange={e => setWeight(e.target.value)} /></label>
          <label className="field"><span>Płeć użyta we wzorze</span><select value={sex} onChange={e => setSex(e.target.value)}><option value="female">Kobieta</option><option value="male">Mężczyzna</option></select></label>
          <label className="field"><span>Aktywność — cały dzień, nie tylko trening</span><select value={activity} onChange={e => setActivity(e.target.value)}>
            <option value="low">Mała — głównie siedzenie, mało ruchu</option><option value="light">Lekka — regularne spacery / lekki ruch</option>
            <option value="moderate">Umiarkowana — dużo chodzenia i regularny trening</option><option value="high">Duża — aktywna praca i intensywny ruch</option>
          </select></label>
          <label className="field"><span>Cel</span><select value={goal} onChange={e => setGoal(e.target.value)}><option value="maintain">Utrzymać masę</option><option value="lose">Schudnąć</option></select></label>
          {goal === "lose" && <><label className="field"><span>Chcę schudnąć (kg)</span><input type="number" min={0.1} step="0.1" value={loss} onChange={e => setLoss(e.target.value)} /></label>
            <label className="field"><span>Propozycja deficytu</span><select value={pace} onChange={e => setPace(e.target.value)}><option value="gentle">Łagodny — około 10%</option><option value="moderate">Umiarkowany — około 15%</option></select></label></>}
        </div>
        <label className="check"><input type="checkbox" checked={eligible} onChange={e => setEligible(e.target.checked)} />Jestem osobą dorosłą; nie dotyczy mnie ciąża, karmienie, zaburzenia odżywiania ani choroba wymagająca indywidualnej diety.</label>
      </fieldset>
      <div><button className="btn" disabled={busy || !eligible} onClick={calculate}>{busy ? "Obliczam…" : "Oblicz propozycję"}</button></div>
      {error && <div className="alert error" role="alert">{error}</div>}
      {result && <div className="card stack" aria-live="polite">
        <h3>Propozycja: {result.targets.kcal.cel} kcal dziennie</h3>
        <p>Białko {result.targets.bialko.cel} g · tłuszcze {result.targets.tluszcz.cel} g · węglowodany {result.targets.weglowodany.cel} g</p>
        <p className="muted">Utrzymanie: około {result.maintenance_kcal} kcal. {goal === "lose" ? `Deficyt: około ${result.deficit_kcal} kcal. Cel: −${result.loss_kg} kg, bez obietnicy terminu.` : "Bez celowego deficytu."}</p>
        <p className="muted">{result.method}</p>
        {result.warnings.map(w => <p className="muted small" key={w}>{w}</p>)}
        <div className="row">{result.sources.map(s => <a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.label}</a>)}</div>
        <div><button className="btn primary" onClick={() => { onApply(result); setResult(null); }}>Wstaw propozycję do formularza</button></div>
        <p className="muted small">Wstawi cele i zakres białka na posiłek. Potem sprawdź wartości i kliknij „Zapisz” na górze strony. Obecny jadłospis nie zostanie przeliczony.</p>
      </div>}
    </div>
  </details>;
}
