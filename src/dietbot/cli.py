"""Wiersz poleceń DietBota (po polsku)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

app = typer.Typer(help="DietBot — tygodniowy jadłospis: LLM układa, deterministyczny silnik liczy.", no_args_is_help=True)
dane_app = typer.Typer(help="Dane: produkty, dania, BLS.", no_args_is_help=True)
plan_app = typer.Typer(help="Plany tygodnia.", no_args_is_help=True)
llm_app = typer.Typer(help="Model językowy: koszty, test połączenia.", no_args_is_help=True)
app.add_typer(dane_app, name="dane")
app.add_typer(plan_app, name="plan")
app.add_typer(llm_app, name="llm")
console = Console()


def _repo():
    from .storage.repository import Repository

    return Repository.load()


@dane_app.command("sprawdz")
def dane_sprawdz() -> None:
    """Sprawdza spójność produktów, dań i założeń."""
    repo = _repo()
    rep = repo.check()
    console.print(f"Produkty: {len(repo.products)}, dania: {len(repo.dishes)}")
    for w in rep.warnings:
        console.print(f"[yellow]uwaga:[/yellow] {w}")
    for e in rep.errors:
        console.print(f"[red]błąd:[/red] {e}")
    if rep.errors:
        raise typer.Exit(1)
    console.print("[green]Dane są spójne.[/green]")


@dane_app.command("importuj-bls")
def dane_importuj_bls() -> None:
    """Buduje indeks BLS 4.0 z pliku XLSX (dane/raw/bls4)."""
    from .food.bls import import_xlsx, write_index
    from .storage.paths import Paths

    paths = Paths.discover()
    foods = import_xlsx(paths.bls_xlsx)
    write_index(foods, paths.bls_index)
    console.print(f"Zaimportowano {len(foods)} produktów BLS do {paths.bls_index}")


@dane_app.command("odswiez-bls")
def dane_odswiez_bls(zapisz: bool = typer.Option(True, help="Zapisz zmiany w pliku produktów")) -> None:
    """Uzupełnia i sprawdza wartości produktów z BLS na podstawie ich kodów."""
    from .food.bls import BlsIndex
    from .food.refresh import refresh_products_file
    from .storage.paths import Paths

    paths = Paths.discover()
    index = BlsIndex.load(paths.bls_index)
    for f in sorted(paths.products_dir.glob("*.yaml")):
        rep = refresh_products_file(f, index, write=zapisz)
        console.print(f"{f.name}: zaktualizowano {len(rep.updated)}, bez zmian {len(rep.unchanged)}")
        for d in rep.diffs:
            console.print(f"  zmiana: {d}")
        for e in rep.errors:
            console.print(f"  [red]błąd:[/red] {e}")


@dane_app.command("szukaj")
def dane_szukaj(nazwa: str) -> None:
    """Pokazuje, jak nazwa produktu dopasowuje się do bazy."""
    from .food.matcher import ProductMatcher

    repo = _repo()
    r = ProductMatcher(repo.products).match(nazwa)
    console.print(f"Status: [bold]{r.status.value}[/bold]  produkt: {r.product_id or '—'}")
    for c in r.candidates:
        console.print(f"  {c.score:5.1f}  {c.product_id:32s} {c.name}  ({c.via})")
    for w in r.warnings:
        console.print(f"[yellow]uwaga:[/yellow] {w}")


@dane_app.command("przelicz-przyklady")
def dane_przelicz_przyklady(bez_llm: bool = typer.Option(False, help="Nie rozstrzygaj niepewnych nazw modelem językowym")) -> None:
    """Przelicza przykładowe jadłospisy z folderu 'diety' i porównuje z sumami podanymi przez czat."""
    from .food.examples import run

    repo = _repo()
    llm = None
    if not bez_llm:
        from .llm.client import LLMClient, LLMError

        try:
            llm = LLMClient(paths=repo.paths)
        except LLMError as exc:
            console.print(f"[yellow]Bez modelu językowego:[/yellow] {exc}")
    fixed = {fx.product_id: fx.grams for fx in repo.assumptions.day.fixed_items}
    report = run(repo.paths.root / "diety", repo.products, fixed, llm)
    out = repo.paths.out / "przyklady_przeliczenie.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8-sig")
    console.print(report)
    console.print(f"Zapisano: {out}")


@dane_app.command("audyt-dan")
def dane_audyt_dan(wycofaj: bool = typer.Option(False, help="Oznacz dania, które nie przeszły testu, jako odrzucone")) -> None:
    """Test silnika dla całej biblioteki: czy z każdym daniem da się ułożyć poprawny dzień."""
    import time

    from .planner.dish_audit import audit_dishes
    from .storage.yamlio import dump_yaml, load_yaml

    repo = _repo()
    dishes = repo.approved_dishes()
    t0 = time.perf_counter()
    passed, failed = audit_dishes(list(dishes.values()), dishes, repo.products, repo.assumptions)
    console.print(f"Sprawdzono {len(passed) + len(failed)} dań w {time.perf_counter() - t0:.0f} s: "
                  f"[green]{len(passed)} w porządku[/green], [red]{len(failed)} do poprawy[/red].")
    for dish, reasons in failed:
        console.print(f"- [bold]{dish.name}[/bold] ({dish.id}): {reasons[0]}")
    if wycofaj and failed:
        ids = {d.id: r[0] for d, r in failed}
        for path in sorted(repo.paths.dishes_dir.glob("*.yaml")):
            data = load_yaml(path)
            touched = False
            for entry in data:
                if entry["id"] in ids and entry.get("status") != "odrzucone":
                    entry["status"] = "odrzucone"
                    entry["zrodlo"] = f"{entry.get('zrodlo', '')} | wycofane przez audyt silnika: {ids[entry['id']]}"
                    touched = True
            if touched:
                dump_yaml(data, path)
        console.print(f"Wycofano {len(ids)} dań (status: odrzucone) — można je przywrócić w zakładce Dania.")


@dane_app.command("wymysl-dania")
def dane_wymysl_dania(
    prosba: str = typer.Argument("", help="Prośba po polsku, np. 'pięć obiadów kuchni meksykańskiej'"),
    plik: Path = typer.Option(None, help="YAML z zamówieniami: [{posilek, kuchnia, ile, szczegoly}]"),
    ile: int = typer.Option(6, help="Najwyżej tyle dań z jednej prośby"),
) -> None:
    """Model wymyśla nowe dania (tylko z produktów z bazy), kod i silnik je sprawdzają, kucharz ocenia."""
    from .domain.dish import MealType
    from .llm.client import LLMClient
    from .llm.generate_dishes import run_generation_plan, save_generated
    from .planner.invent import invent_dishes
    from .storage.yamlio import load_yaml, to_plain

    repo = _repo()
    llm = LLMClient(paths=repo.paths)
    if plik:
        orders = [(MealType(o["posilek"]), o["kuchnia"], int(o.get("ile", 5)), o.get("szczegoly"))
                  for o in to_plain(load_yaml(plik))]
        accepted, rejected, cost = run_generation_plan(repo, llm, plan=orders, workers=8, log=console.print,
                                                       review=True, engine_check=True)
    elif prosba.strip():
        result = invent_dishes(repo, llm, prosba, max_total=max(1, min(ile, 12)), log=console.print)
        accepted, rejected, cost = result.accepted, result.rejected, result.cost_usd
    else:
        raise typer.BadParameter("podaj prośbę albo --plik z zamówieniami")
    if accepted:
        counts = save_generated(accepted, repo.paths.dishes_dir)
        console.print(f"[green]Zapisano {len(accepted)} dań[/green]: {counts}")
    console.print(f"Odrzucone propozycje: {len(rejected)}; koszt {cost:.4f} $")


@plan_app.command("licz")
def plan_licz(plik: Path = typer.Argument(..., help="YAML/JSON z listą slotów (dni, posiłek, kandydaci)"),
              bez_przekasek: bool = typer.Option(False, help="Nie dodawaj przekąsek bilansujących")) -> None:
    """Liczy gramatury dla podanych dań i zapisuje jadłospis (JSON + Markdown) w folderze wyniki."""
    from .checker import verify_plan
    from .engine import EngineRequest, solve_week
    from .report.markdown import render_plan, render_shopping_list
    from .storage.yamlio import load_yaml, to_plain

    repo = _repo()
    raw = to_plain(load_yaml(plik)) if plik.suffix in (".yaml", ".yml") else json.loads(plik.read_text("utf-8"))
    slots = raw["sloty"] if isinstance(raw, dict) and "sloty" in raw else raw
    request = EngineRequest.model_validate({"slots": slots, "allow_snacks": not bez_przekasek})
    result = solve_week(request, repo.assumptions, repo.products, repo.dishes)
    _print_blocks(result)
    if result.plan is None:
        raise typer.Exit(1)
    engine_totals = [t.model_dump(mode="json") for b in result.blocks for t in b.totals]
    report = verify_plan(result.plan, repo.assumptions, repo.products, repo.dishes, engine_totals)
    _save_outputs(repo, result, report, render_plan, render_shopping_list)


def _print_blocks(result) -> None:
    table = Table(title=f"Silnik: {result.status.value}")
    for col in ("Dni", "Status", "Czas [ms]", "Uwagi"):
        table.add_column(col)
    for b in result.blocks:
        notes = []
        notes += [f"{v.metric} {v.person_id} {v.gap:+}" for v in b.violations]
        notes += [f"kotwica {a.person_id} {a.gap_g:+}" for a in b.anchor_violations]
        if b.message:
            notes.append(b.message)
        table.add_row(",".join(str(int(d)) for d in b.days), b.status.value, f"{b.wall_ms:.0f}", "; ".join(notes)[:80])
    console.print(table)


def _save_outputs(repo, result, report, render_plan, render_shopping_list) -> None:
    out = repo.paths.out
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out / f"plan_{stamp}.json").write_text(result.plan.model_dump_json(by_alias=True, indent=2), encoding="utf-8")
    md = render_plan(result.plan, repo.assumptions, repo.products, repo.dishes, report)
    (out / f"jadlospis_{stamp}.md").write_text(md, encoding="utf-8-sig")
    shop = render_shopping_list(result.plan, repo.assumptions, repo.products, repo.store_products)
    (out / f"zakupy_{stamp}.md").write_text(shop, encoding="utf-8-sig")
    console.print(f"Weryfikator: {'[green]OK[/green]' if report.ok else '[red]są błędy[/red]'} "
                  f"({len(report.issues)} uwag). Zapisano: wyniki/jadlospis_{stamp}.md, wyniki/zakupy_{stamp}.md")


@app.command("aplikacja")
def aplikacja(bez_telegrama: bool = typer.Option(False, help="Nie uruchamiaj bota Telegram")) -> None:
    """Otwiera aplikację w oknie (lokalny serwer + okno). Bot Telegram startuje, jeśli jest token w .env."""
    from .app.desktop import run_desktop

    run_desktop(with_telegram=not bez_telegrama)


@app.command("serwer")
def serwer(port: int = typer.Option(8765, help="Port lokalnego serwera (tylko 127.0.0.1)"),
           przegladarka: bool = typer.Option(True, help="Otwórz aplikację w przeglądarce"),
           bez_telegrama: bool = typer.Option(False, help="Nie uruchamiaj bota Telegram")) -> None:
    """Uruchamia aplikację jako lokalną stronę w przeglądarce."""
    from .app.desktop import run_server_only

    run_server_only(port=port, open_browser=przegladarka, with_telegram=not bez_telegrama)


@app.command("telegram")
def telegram() -> None:
    """Uruchamia tylko bota Telegram (token i dozwolone ID w pliku .env)."""
    import asyncio

    from .app.service import DietService
    from .app.telegram_bot import run_bot

    asyncio.run(run_bot(DietService()))


@plan_app.command("generuj")
def plan_generuj(zyczenia: str = typer.Option("", help="Życzenia do planu, np. 'więcej ryb'"),
                 bez_llm: bool = typer.Option(False, help="Ułóż tydzień algorytmem, bez modelu językowego")) -> None:
    """Układa nowy plan tygodnia (jak w aplikacji) i zapisuje go wraz z plikami Markdown."""
    from .app.service import DietService

    service = DietService(use_llm=not bez_llm)
    job = service.generate(wishes=zyczenia or None, use_llm=not bez_llm)
    seen = 0
    while True:
        job = service.jobs.get(job.id)
        for line in job.log[seen:]:
            console.print(line)
        seen = len(job.log)
        if job.status.value in ("gotowe", "blad"):
            break
        import time

        time.sleep(0.3)
    if job.status.value == "blad":
        console.print(f"[red]Błąd:[/red] {job.error}")
        raise typer.Exit(1)
    out = service.export(job.result["plan_id"])
    console.print(f"Zapisano: {out['plan_path']}")
    console.print(f"          {out['shopping_path']}")


@llm_app.command("koszty")
def llm_koszty() -> None:
    """Pokazuje wydatki na model językowy względem budżetu."""
    from .llm.client import CostLedger, LLMSettings
    from .storage.paths import Paths

    paths = Paths.discover()
    settings = LLMSettings.load(paths)
    ledger = CostLedger(paths.out / "llm_koszty.jsonl", settings)
    spent = ledger.spent_usd()
    console.print(f"Wydano {spent:.4f} $ z budżetu {settings.budget_usd:.2f} $ ({spent / settings.budget_usd:.1%}).")


if __name__ == "__main__":
    app()
