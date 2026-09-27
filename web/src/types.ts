// Typy odpowiedzi API DietBota (src/dietbot/app/views.py i service.py).

export type Metric = "kcal" | "protein" | "fat" | "carbs";
export type BandState = "ok" | "za_malo" | "za_duzo";

export interface Band {
  min: number;
  max: number;
  target: number;
}

export interface Person {
  id: string;
  name: string;
  targets: Record<Metric, Band>;
  anchor: { min: number; max: number; tolerance: number };
}

export interface Nutrition {
  kcal: number;
  protein: number;
  fat: number;
  carbs: number;
  fiber: number;
}

export interface PlanItem {
  product_id: string;
  name: string;
  category: string | null;
  grams: number;
  amount: string;
  household: string | null;
  negligible: boolean;
}

export interface Portion {
  items: PlanItem[];
  nutrition: Nutrition;
  anchor_state: BandState | null;
  mass_g: number;
  mass_cap_g: number | null;
  mass_over: boolean;
}

export interface PotItem {
  product_id: string;
  name: string;
  amount: string;
  household: string | null;
}

export interface GroupItem {
  product_id: string;
  name: string;
  amount: string;
  household: string | null;
  share_pct: number;
  recipe_pct: number;
}

export interface MealGroup {
  name: string;
  total_g: number;
  items: GroupItem[];
}

export interface Meal {
  slot_key: string;
  meal: string;
  meal_label: string;
  dish_id: string;
  dish_name: string;
  description: string | null;
  cuisine: string | null;
  tags: string[];
  is_treat: boolean;
  is_sweet: boolean;
  prep_minutes: number | null;
  steps: string[];
  batch_group: string | null;
  batch_days: string[];
  reheat: boolean;
  pot: PotItem[] | null;
  portions: Record<string, Portion>;
  groups: MealGroup[];
}

export interface Snack {
  person_id: string;
  dish_id: string;
  dish_name: string;
  items: PlanItem[];
  reason: string | null;
  nutrition: Nutrition;
}

export interface Issue {
  code: string;
  severity: "blad" | "ostrzezenie";
  message: string;
  day: number | null;
  person_id: string | null;
  meal: string | null;
}

export interface DayTotals {
  values: Nutrition;
  states: Record<Metric, BandState>;
}

export interface BreakdownRow {
  kind: "posilek" | "przekaska" | "stale";
  label: string;
  values: Record<Metric, number>;
}

export interface DayBreakdown {
  rows: BreakdownRow[];
  total: Record<Metric, number>;
  states: Record<Metric, BandState>;
  consistent: boolean;
}

export interface Day {
  day: number;
  label: string;
  meals: Meal[];
  snacks: Snack[];
  totals: Record<string, DayTotals>;
  breakdown?: Record<string, DayBreakdown>;
  issues: Issue[];
}

export interface WeekSummary {
  person_id: string;
  days: { day: number; label: string; values: Record<Metric, number>; states: Record<Metric, BandState> }[];
  total: Record<Metric, number>;
  average: Record<Metric, number>;
  average_states: Record<Metric, BandState>;
  total_states: Record<Metric, BandState>;
  week_band: Record<Metric, [number, number]>;
  days_ok: number;
  days_count: number;
}

export interface Fact {
  label: string;
  value: string;
  detail: string;
  ok: boolean;
}

export interface PlanSummary {
  saved_diet?: { plan_id: string; title: string; saved_at: string } | null;
  id: string;
  title: string;
  created_at: string;
  source: string;
  ok: boolean;
  version: number;
  family_id: string;
  parent_id: string | null;
  change_note: string | null;
  wishes: string | null;
  week_start: string | null;
  llm_cost_usd: number;
  seconds: number;
  active?: boolean;
}

export interface PlanView extends PlanSummary {
  snapshot_legacy?: boolean;
  snapshot_date?: string;
  source_label: string;
  comment: string | null;
  persons: Person[];
  metric_labels: Record<string, string>;
  days: Day[];
  fixed_items: { product_id: string; name: string; grams: number; description: string | null }[];
  facts: Fact[];
  week_summary?: WeekSummary[];
  issues: Issue[];
  week_issues: Issue[];
  log: string[];
  engine: string | null;
  assumptions_changed: boolean;
  active: boolean;
  versions: PlanSummary[];
}

export type JobState = "w_kolejce" | "w_toku" | "gotowe" | "blad";

export interface Job {
  id: string;
  kind: string;
  title: string;
  status: JobState;
  log: string[];
  log_size: number;
  result: {
    plan_id?: string;
    ok?: boolean;
    rule_warnings?: string[];
    new_dishes?: string[];
    dishes?: { id: string; name: string; meal_types: string[] }[];
  } | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Status {
  version: string;
  products: number;
  dishes: number;
  approved_dishes: number;
  pools: Record<string, number>;
  batch_pools: Record<string, number>;
  store_products: number;
  data_errors: string[];
  data_warnings: string[];
  llm: { provider: string; model: string; key_present: boolean; enabled: boolean; spent_usd: number; budget_usd: number };
  telegram: { configured: boolean; allowed_users: number };
  active_plan_id: string | null;
  jobs_busy: boolean;
  persons: Person[];
  default_week_start: string;
}

export interface SwapOption {
  id: string;
  name: string;
  description: string | null;
  cuisine: string;
  tags: string[];
  prep_minutes: number | null;
  profile: DishProfile | null;
  notes: string[];
}

export interface SwapOptions {
  slot_key: string;
  meal: string;
  meal_label: string;
  days: string[];
  current: { id: string; name: string };
  options: SwapOption[];
}

export interface ShoppingItem {
  produkt: string;
  nazwa: string;
  kategoria: string;
  kategoria_nazwa: string;
  gramy: number;
  ilosc: string;
  pomijalny: boolean;
  sklep: { id: string; sklep: string; nazwa: string; marka: string | null; opakowanie_g: number | null; uwagi: string | null } | null;
  opakowania: number | null;
  opakowania_tekst: string | null;
  na_wynos?: boolean;
}

export interface Shopping {
  plan_id: string;
  title: string;
  days: number;
  persons: number;
  groups: { category: string; label: string; items: ShoppingItem[] }[];
}

export interface DishProfile {
  protein_sources: string[];
  carb_sources: string[];
  protein_label: string;
  energy_label: string;
  protein_range: Record<string, [number, number]>;
}

export interface DishListItem {
  diets: string[];
  id: string;
  name: string;
  description: string | null;
  meal_types: string[];
  cuisine: string;
  tags: string[];
  status: string;
  prep_minutes: number | null;
  in_pool: boolean;
  excluded: string[];
  protein_label: string | null;
  energy_label: string | null;
  invented: boolean;
  has_groups?: boolean;
}

export interface DishDetail {
  id: string;
  name: string;
  description: string | null;
  meal_types: string[];
  meal_labels: string[];
  cuisine: string;
  tags: string[];
  status: string;
  prep_minutes: number | null;
  is_treat: boolean;
  is_sweet: boolean;
  is_batch: boolean;
  batch_days: number | null;
  source: string | null;
  steps: string[];
  ingredients: {
    product_id: string;
    name: string;
    role: string;
    portioning: string;
    typical_g: number;
    min_g: number | null;
    max_g: number | null;
    optional: boolean;
    household: string | null;
  }[];
  groups: { name: string; tolerance_pct: number; items: { product_id: string; name: string; share_pct: number }[] }[];
  profile: DishProfile | null;
  excluded: string[];
  person_names?: Record<string, string>;
}

export interface Per100 {
  kcal: number;
  protein: number;
  fat: number;
  carbs: number;
  fiber: number | null;
  sugars: number | null;
  salt: number | null;
}

export interface ProductListItem {
  id: string;
  name: string;
  category: string;
  category_label: string;
  per_100g: Per100;
  negligible: boolean;
  source: string;
}

export interface ProductDetail extends Omit<ProductListItem, "source"> {
  aliases: string[];
  state: string;
  source: Record<string, string>;
  measures: { unit: string; grams: number; description: string | null; source: string | null }[];
  step_g: number;
  max_portion_g: number | null;
  density_g_per_ml: number | null;
  notes: string | null;
  used_in: { id: string; name: string }[];
  stores: Record<string, unknown>[];
}

export interface MatchResult {
  query: string;
  status: "pewne" | "niepewne" | "brak";
  product_id: string | null;
  candidates: { product_id: string; name: string; score: number; via: string }[];
  warnings: string[];
}

export interface AssumptionsResponse {
  data: AssumptionsData;
  warnings: string[];
  snack_options: { id: string; name: string; diets: string[] }[];
  products: { id: string; name: string; diets: string[] }[];
  categories: { id: string; name: string }[];
  cuisines: string[];
  tags: string[];
  meals: { id: string; name: string }[];
}

export interface BandData {
  min: number;
  max: number;
  cel?: number;
}

export interface PersonData {
  id: string;
  nazwa: string;
  cele_dzienne: { kcal: BandData; bialko: BandData; tluszcz: BandData; weglowodany: BandData };
  kotwica_bialka_na_posilek: BandData;
  tolerancja_kotwicy_g?: number;
  wyklucza_produkty?: string[];
  maks_masa_posilku_g?: Record<string, number>;
}

export interface AssumptionsData {
  rodzaj_diety?: "tradycyjna" | "wege" | "wegan";
  wersja?: number;
  energia?: { metoda: string };
  osoby: PersonData[];
  dzien: {
    posilki: string[];
    kolacja_lzejsza_niz_obiad?: boolean;
    stale_pozycje?: { produkt: string; gramy: number; opis?: string }[];
    przekaski?: { max_na_osobe_dziennie?: number; dozwolone?: string[] };
  };
  tydzien: {
    posilki_20_procent: BandData;
    slodkie_kolacje_max?: number;
    na_zapas?: { tag: string; posilek: string; dni: string[] }[];
    bez_powtorek?: boolean;
  };
  zakazy?: {
    produkty?: string[];
    tagi_dan_w_posilkach?: Record<string, string[]>;
    dozwolone_zrodla_weglowodanow?: string[];
    kolacja_bez_weglowodanow_z?: string[];
  };
  kuchnie?: string[];
  [key: string]: unknown;
}

export interface ChatMessage {
  id: number;
  role: "uzytkownik" | "asystent";
  text: string;
  created_at?: string;
  plan_id?: string | null;
  data: { job_id?: string; kind?: string; proposal_id?: string; diff?: string[]; plan_id?: string } | null;
}

export interface Costs {
  spent_usd: number;
  budget_usd: number;
  model: string;
  by_task: { task: string; calls: number; cost_usd: number; failed: number }[];
  recent: Record<string, unknown>[];
}
