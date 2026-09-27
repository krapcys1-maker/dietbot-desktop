import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import Icon from "./Icon";
import { useToast } from "./ui";

const RatingsContext = createContext<{
  ratings: Record<string, number>;
  ready: boolean;
  busy: string[];
  rate: (id: string, rating: number) => Promise<void>;
}>({ ratings: {}, ready: false, busy: [], rate: async () => undefined });

export function RatingsProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [ratings, setRatings] = useState<Record<string, number>>({});
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState<string[]>([]);
  const pending = useRef(new Set<string>());
  useEffect(() => {
    let alive = true;
    api.ratings().then((data) => { if (alive) { setRatings(data); setReady(true); } })
      .catch((e: Error) => toast(`Nie udało się wczytać ocen: ${e.message}`, "error"));
    return () => { alive = false; };
  }, []);
  const rate = async (id: string, rating: number) => {
    if (pending.current.has(id)) return;
    pending.current.add(id);
    setBusy([...pending.current]);
    try {
      await api.rateDish(id, rating);
      setRatings((old) => ({ ...old, [id]: rating }));
      toast(rating === 1 ? "Lubię — bot będzie częściej wybierał to danie, jeśli pasuje do planu."
        : rating === -1 ? "Nie lubię — bot nie zaproponuje tego dania w nowych planach ani zamianach."
        : "Ocena cofnięta — danie znów jest neutralne.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      pending.current.delete(id);
      setBusy([...pending.current]);
    }
  };
  return <RatingsContext.Provider value={{ ratings, ready, busy, rate }}>{children}</RatingsContext.Provider>;
}

export const useRatings = () => useContext(RatingsContext);

export default function DishRating({ id, name }: { id: string; name: string }) {
  const { ratings, ready, busy, rate } = useRatings();
  const rating = ratings[id] ?? 0;
  return (
    <div className="row no-print rating-actions" style={{ gap: 6 }}>
      <button className={`btn small ${rating === 1 ? "primary" : ""}`} disabled={!ready || busy.includes(id)}
        aria-label={`Lubię: ${name}`} aria-pressed={rating === 1}
        title="Proponuj częściej. Ponowne kliknięcie cofa ocenę."
        onClick={() => void rate(id, rating === 1 ? 0 : 1)}><Icon name="like" />Lubię</button>
      <button className={`btn small ${rating === -1 ? "danger" : ""}`} disabled={!ready || busy.includes(id)}
        aria-label={`Nie lubię: ${name}`} aria-pressed={rating === -1}
        title="Nie proponuj w nowych planach i zamianach. Ponowne kliknięcie cofa ocenę."
        onClick={() => void rate(id, rating === -1 ? 0 : -1)}><Icon name="dislike" />Nie lubię{rating === -1 ? " ✓" : ""}</button>
    </div>
  );
}
