import { useRef, useState } from "react";
import { api } from "../api";
import Icon from "./Icon";
import { useToast } from "./ui";

export default function ExportButtons({ planId, shoppingOnly = false }: { planId: string; shoppingOnly?: boolean }) {
  const toast = useToast();
  const pending = useRef(false);
  const [busy, setBusy] = useState<"plan" | "shopping" | null>(null);
  const save = async (kind: "plan" | "shopping") => {
    if (pending.current) return;
    pending.current = true;
    setBusy(kind);
    try {
      const result = await api.savePlanFile(planId, kind);
      toast(result.saved ? `Zapisano: ${result.path}` : "Anulowano zapis.");
    } catch (e) { toast((e as Error).message, "error"); }
    finally { pending.current = false; setBusy(null); }
  };
  return <div className="row export-actions no-print">
    {!shoppingOnly && <button className="btn primary" disabled={busy !== null} onClick={() => void save("plan")}
      title="Zapisz wszystkie dni z gramaturami i przepisami do TXT. Wybierzesz folder i nazwę pliku.">
      <Icon name="download" />{busy === "plan" ? "Zapisywanie…" : "Eksportuj dietę"}
    </button>}
    <button className={`btn ${shoppingOnly ? "primary" : ""}`} disabled={busy !== null} onClick={() => void save("shopping")}
      title="Zapisz całą listę zakupów do TXT, również odhaczone produkty. Wybierzesz folder i nazwę pliku.">
      <Icon name="download" />{busy === "shopping" ? "Zapisywanie…" : "Eksportuj listę zakupów"}
    </button>
  </div>;
}
