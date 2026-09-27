import { useState } from "react";
import { api } from "../api";
import Icon from "./Icon";
import type { PlanSummary } from "../types";
import { Modal, useToast } from "./ui";

export default function SaveDietButton({ plan, onSaved }: { plan: PlanSummary; onSaved: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.saveDiet(plan.id, title.trim());
      toast("Dieta zapisana w zakładce Moje diety.");
      setOpen(false);
      onSaved();
    } catch (e) { toast((e as Error).message, "error"); }
    finally { setBusy(false); }
  };
  return <>
    <button className="btn" onClick={() => { setTitle(plan.saved_diet?.title ?? plan.title); setOpen(true); }}>
      <Icon name="bookmark" />{plan.saved_diet ? "Zmień nazwę zapisanej diety" : "Zapisz dietę"}
    </button>
    {open && <Modal title="Zapisz dietę w aplikacji" onClose={() => { if (!busy) setOpen(false); }}>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label>Nazwa diety
          <input type="text" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} minLength={2} maxLength={80}
            required style={{ width: "100%", marginTop: 6 }} />
        </label>
        <p>Zapisujesz cały tydzień — wersję {plan.version}, ze wszystkimi daniami i gramaturami.
          Późniejsze zamiany utworzą nową wersję, nie nadpiszą tej zapisanej.</p>
        <div className="row">
          <button className="btn primary" disabled={busy || title.trim().length < 2} type="submit">{busy ? "Zapisuję…" : "Zapisz dietę"}</button>
          <button className="btn" type="button" disabled={busy} onClick={() => setOpen(false)}>Anuluj</button>
        </div>
      </form>
    </Modal>}
  </>;
}
