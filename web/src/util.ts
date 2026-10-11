import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

// ---------------------------------------------------------------- formatowanie po polsku
export function num(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("pl-PL", { minimumFractionDigits: 0, maximumFractionDigits: decimals });
}

export function usd(value: number): string {
  return `${value.toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 4 })} $`;
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n);
  if (a === 1) return one;
  if (a % 10 >= 2 && a % 10 <= 4 && !(a % 100 >= 12 && a % 100 <= 14)) return few;
  return many;
}

export const DAY_SHORT = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"];
export const DAY_NAMES = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"];
export const MEAL_NAMES: Record<string, string> = { sniadanie: "Śniadanie", obiad: "Obiad", kolacja: "Kolacja", przekaska: "Przekąska" };
export const METRIC_NAMES: Record<string, string> = { kcal: "Energia", protein: "Białko", fat: "Tłuszcz", carbs: "Węglowodany" };
export const TAG_NAMES: Record<string, string> = {
  wegan: "wegańskie",
  slodkie: "słodkie", pula_20: "pula 20%", na_zapas: "na zapas", zupa: "zupa", wege: "wege", na_cieplo: "na ciepło", szybkie: "szybkie",
  na_zimno: "na zimno", przekaska: "przekąska", fit: "fit",
};
export const STATE_NAMES: Record<string, string> = {
  surowy: "surowy", suchy: "suchy", gotowany: "gotowany", gotowy_do_jedzenia: "gotowy do jedzenia", odsaczony: "odsączony",
  mrozony: "mrożony", pieczony: "pieczony", smazony: "smażony", wedzony: "wędzony", konserwowy: "konserwowy",
};
export const STATUS_NAMES: Record<string, string> = { szkic: "szkic", zatwierdzone: "zatwierdzone", odrzucone: "odrzucone" };

/** Który dzień planu (0–6) jest dziś, jeśli plan obejmuje dzisiejszą datę. */
export function todayIndex(weekStart: string | null): number | null {
  const now = new Date();
  if (!weekStart) return null;
  const [y, m, d] = weekStart.split("-").map(Number);
  const start = new Date(y, m - 1, d);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diff = Math.round((today.getTime() - start.getTime()) / 86400000);
  return diff >= 0 && diff < 7 ? diff : null;
}

export function dayDate(weekStart: string | null, index: number): string | null {
  if (!weekStart) return null;
  const [y, m, d] = weekStart.split("-").map(Number);
  const date = new Date(y, m - 1, d + index);
  return date.toLocaleDateString("pl-PL", { day: "numeric", month: "numeric" });
}

// ---------------------------------------------------------------- router na hashu (#/tydzien/abc)
// Strażnik niezapisanych zmian: przejście do innej zakładki pyta o zgodę, a po odmowie wraca pod poprzedni adres.
// Słuchacz rejestruje się przy wczytaniu modułu, więc działa przed odświeżeniem widoku przez React.
let leaveGuard: string | null = null;
let lastHash = typeof window !== "undefined" ? window.location.hash : "";
let reverting = false;
if (typeof window !== "undefined") {
  window.addEventListener("hashchange", () => {
    if (reverting) { reverting = false; return; }
    if (leaveGuard && !window.confirm(leaveGuard)) {
      reverting = true;
      window.location.hash = lastHash;
      return;
    }
    lastHash = window.location.hash;
  });
}

export function setLeaveGuard(message: string | null) {
  leaveGuard = message;
}

function subscribe(cb: () => void) {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
}

export function useRoute(): string[] {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  const path = hash.replace(/^#\/?/, "");
  const decode = (part: string) => {
    try {
      return decodeURIComponent(part);
    } catch {
      return part; // zepsuty adres (np. samotny znak %) nie może wywalić całej aplikacji
    }
  };
  return path ? path.split("/").map(decode) : [];
}

export function go(...parts: string[]) {
  window.location.hash = "/" + parts.filter(Boolean).map(encodeURIComponent).join("/");
}

// ---------------------------------------------------------------- dane asynchroniczne
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => {
        if (alive) {
          setData(d);
          setError(null);
        }
      })
      .catch((e: Error) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload, setData };
}

// ---------------------------------------------------------------- pamięć w przeglądarce (bezpieczna)
export function loadLocal<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function saveLocal(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* pamięć niedostępna — trudno */
  }
}
