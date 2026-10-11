// Klient API — wszystkie błędy zamieniane na czytelne komunikaty po polsku.
import type {
  AssumptionsData,
  AssumptionsResponse,
  ChatMessage,
  Costs,
  DishDetail,
  DishListItem,
  Job,
  NotebookView,
  PlanSummary,
  PlanView,
  ProductDetail,
  ProductListItem,
  RuleInput,
  Shopping,
  Status,
  SwapOptions,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Token sesji: okno aplikacji otwiera stronę z ?t=…; zapamiętujemy go na czas sesji i usuwamy z paska adresu.
const TOKEN_KEY = "porcja:token";
function readSessionToken(): string | null {
  let token: string | null = null;
  try {
    const params = new URLSearchParams(window.location.search);
    token = params.get("t");
    if (token) {
      params.delete("t");
      const rest = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
      sessionStorage.setItem(TOKEN_KEY, token);
      return token;
    }
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return token; // pamięć sesji niedostępna — token zostaje tylko w pamięci strony
  }
}
const SESSION_TOKEN = typeof window === "undefined" ? null : readSessionToken();

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (SESSION_TOKEN) headers["X-Porcja-Token"] = SESSION_TOKEN;
  try {
    res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Brak połączenia z Porcją. Sprawdź, czy aplikacja jest uruchomiona, i spróbuj ponownie.", 0);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (res.status === 401) {
    // strona otwarta bez tokenu sesji (np. adres wpisany w przeglądarce) — App pokaże wyjaśnienie zamiast pustego ekranu
    window.dispatchEvent(new CustomEvent("porcja:brak-dostepu"));
  }
  if (!res.ok) {
    let msg = `Błąd ${res.status}`;
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === "string") msg = detail;
      else if (Array.isArray(detail)) msg = detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
    }
    throw new ApiError(msg, res.status);
  }
  return data as T;
}

const q = (params: Record<string, string | undefined>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) s.set(k, v);
  const str = s.toString();
  return str ? `?${str}` : "";
};

export const api = {
  calculateGoals: (body: Record<string, unknown>) => request<import("./components/GoalCalculator").GoalEstimate>("POST", "/api/cele/oblicz", body),
  status: () => request<Status>("GET", "/api/status"),
  job: (id: string, since = 0) => request<Job>("GET", `/api/zadania/${id}?od=${since}`),
  cancelJob: (id: string) => request<Job>("POST", `/api/zadania/${id}/przerwij`),

  generate: (body: { wishes?: string; days?: number[]; avoid_recent: boolean; use_llm: boolean; week_start?: string; invent?: boolean }) =>
    request<Job>("POST", "/api/plany/generuj", body),
  plans: () => request<PlanSummary[]>("GET", "/api/plany"),
  library: () => request<PlanSummary[]>("GET", "/api/diety"),
  saveDiet: (id: string, title: string) => request<NonNullable<PlanSummary["saved_diet"]>>("PUT", `/api/diety/${id}`, { title }),
  unsaveDiet: (id: string) => request<{ ok: boolean }>("DELETE", `/api/diety/${id}`),
  ratings: () => request<Record<string, number>>("GET", "/api/oceny"),
  rateDish: (id: string, rating: number) => request<{ dish_id: string; rating: number }>("PUT", `/api/oceny/${id}`, { rating }),
  activePlan: () => request<PlanView>("GET", "/api/plany/aktywny"),
  plan: (id: string) => request<PlanView>("GET", `/api/plany/${id}`),
  patchPlan: (id: string, body: { title?: string; active?: boolean }) => request<PlanView>("PATCH", `/api/plany/${id}`, body),
  deletePlan: (id: string) => request<{ ok: boolean }>("DELETE", `/api/plany/${id}`),
  swapOptions: (id: string, slot: string) => request<SwapOptions>("GET", `/api/plany/${id}/zamienniki${q({ slot })}`),
  swap: (id: string, slot_key: string, dish_id: string) => request<Job>("POST", `/api/plany/${id}/zamien`, { slot_key, dish_id }),
  recompute: (id: string, repair: boolean) => request<Job>("POST", `/api/plany/${id}/przelicz`, { repair }),
  shopping: (id: string) => request<Shopping>("GET", `/api/plany/${id}/zakupy`),
  savePlanFile: (id: string, kind: "plan" | "shopping") =>
    request<{ saved: boolean; path: string | null; filename: string }>("POST", `/api/plany/${id}/zapisz-plik`, { kind }),

  assumptions: () => request<AssumptionsResponse>("GET", "/api/zalozenia"),
  checkAssumptions: (data: AssumptionsData) =>
    request<{ ok: boolean; errors: string[]; warnings: string[] }>("POST", "/api/zalozenia/sprawdz", { data }),
  saveAssumptions: (data: AssumptionsData, version?: string, confirm_low_kcal = false) =>
    request<{ ok: boolean; errors: string[]; warnings: string[]; needs_confirmation?: string; version?: string }>(
      "PUT", "/api/zalozenia", { data, version, confirm_low_kcal }),

  dishes: (params: { posilek?: string; q?: string; status?: string }) => request<DishListItem[]>("GET", `/api/dania${q(params)}`),
  dish: (id: string) => request<DishDetail>("GET", `/api/dania/${id}`),
  setDishStatus: (id: string, status: string) => request<DishDetail>("PATCH", `/api/dania/${id}`, { status }),
  inventDishes: (requestText: string, count: number) => request<Job>("POST", "/api/dania/wymysl", { request: requestText, count }),

  products: (params: { q?: string; kategoria?: string }) => request<ProductListItem[]>("GET", `/api/produkty${q(params)}`),
  product: (id: string) => request<ProductDetail>("GET", `/api/produkty/${id}`),
  addProduct: (body: Record<string, unknown>) => request<ProductDetail>("POST", "/api/produkty", body),

  chatHistory: () => request<ChatMessage[]>("GET", "/api/czat"),
  chat: (text: string, plan_id?: string) => request<ChatMessage>("POST", "/api/czat", { text, plan_id }),
  chatConfirm: (proposal_id: string) => request<ChatMessage>("POST", "/api/czat/potwierdz", { proposal_id }),
  chatReject: (proposal_id: string) => request<{ ok: boolean }>("POST", "/api/czat/odrzuc", { proposal_id }),

  notebook: () => request<NotebookView>("GET", "/api/notatnik"),
  addNote: (text: string, plan_id?: string) => request<NotebookView>("POST", "/api/notatnik/uwaga", { text, plan_id }),
  addRule: (rule: RuleInput) => request<NotebookView>("POST", "/api/notatnik/zasady", rule),
  setRuleActive: (id: string, active: boolean) => request<NotebookView>("PATCH", `/api/notatnik/zasady/${id}`, { active }),
  deleteRule: (id: string) => request<NotebookView>("DELETE", `/api/notatnik/zasady/${id}`),
  analyzeHabits: () => request<NotebookView>("POST", "/api/notatnik/nawyki"),
  applyProposal: (id: string) => request<NotebookView>("POST", `/api/notatnik/propozycje/${id}`),
  programNoteFromHabits: (index: number) => request<NotebookView>("POST", `/api/notatnik/do-poprawy/z-analizy/${index}`),
  setProgramNote: (id: string, status: "nowa" | "zrobione" | "usunieta") =>
    request<NotebookView>("PATCH", `/api/notatnik/do-poprawy/${id}`, { status }),
  notebookText: () => request<{ content: string }>("GET", "/api/notatnik/tekst"),
  saveNotebookFile: () => request<{ saved: boolean; path: string | null; filename: string }>("POST", "/api/notatnik/zapisz-plik"),

  costs: () => request<Costs>("GET", "/api/koszty"),
  saveDeepSeekKey: (key: string) => request<Status>("PUT", "/api/ustawienia/deepseek", { key }),
  deleteDeepSeekKey: () => request<Status>("DELETE", "/api/ustawienia/deepseek"),
  openResults: () => request<{ ok: boolean; path: string }>("POST", "/api/system/otworz-wyniki"),
};

/** Czeka na koniec zadania, przekazując nowe wpisy dziennika. */
export async function followJob(id: string, onLog: (lines: string[], job: Job) => void, signal?: AbortSignal): Promise<Job> {
  let since = 0;
  for (;;) {
    if (signal?.aborted) throw new ApiError("Przerwano", 0);
    const job = await api.job(id, since);
    if (job.log.length) onLog(job.log, job);
    since += job.log.length; // nie log_size: linia dopisana między wycięciem a policzeniem dziennika by przepadła
    if (job.status === "gotowe" || job.status === "blad" || job.status === "przerwane") return job;
    await new Promise((r) => setTimeout(r, 600));
  }
}
