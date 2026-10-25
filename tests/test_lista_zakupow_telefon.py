"""Lista zakupów do notatek w telefonie (25.10.2026): cztery działy i żadnych pustych linii.

Domownik wkleja listę do notatek, które robią z każdego wiersza pozycję do odhaczania — puste linie, akapity
objaśnień i kilkanaście kategorii (sery, sosy i dodatki, makaron, ryż…) tylko przeszkadzały.
"""
from fixtures_week import WEEK_SLOTS

from dietbot.domain.product import Category
from dietbot.engine import EngineRequest, solve_week
from dietbot.shopping import SHOP_GROUPS, build_shopping_list, render_shopping_text, shop_group


def _rows(repo):
    res = solve_week(EngineRequest.model_validate({"slots": WEEK_SLOTS}), repo.assumptions, repo.products,
                     repo.dishes)
    return build_shopping_list(res.plan, repo.assumptions, repo.products, repo.store_products)


def test_only_four_groups_in_fixed_order(repo):
    rows = _rows(repo)
    labels = [label for _, label in SHOP_GROUPS]
    assert labels == ["Nabiał", "Mięso", "Owoce i warzywa", "Inne"]
    seen = []
    for r in rows:
        if r.group_label not in seen:
            seen.append(r.group_label)
    assert seen == [label for label in labels if label in seen] and set(seen) <= set(labels)


def test_phone_text_has_no_blank_lines_or_notes(repo):
    text = render_shopping_text(_rows(repo), "Tydzień testowy")
    lines = text.rstrip("\n").split("\n")
    assert lines[0] == "Lista zakupów — Tydzień testowy"
    assert all(line.strip() for line in lines), "pusta linia w liście do telefonu"
    assert not any(line.startswith(("- ", "#", "[")) for line in lines)
    assert "Warianty produktów" not in text and "Uproszczona nazwa" not in text
    headers = [line for line in lines[1:] if line.isupper()]
    assert headers and set(headers) <= {"NABIAŁ", "MIĘSO", "OWOCE I WARZYWA", "INNE"}


def test_store_sections(repo):
    p = repo.products
    assert shop_group("jajko_kurze", p["jajko_kurze"].category) == "nabial"
    assert shop_group("gouda", p["gouda"].category) == "nabial"
    assert shop_group("szynka_gotowana", p["szynka_gotowana"].category) == "mieso"
    assert shop_group("ziemniaki", p["ziemniaki"].category) == "owoce_warzywa"
    assert shop_group("pomidor", p["pomidor"].category) == "owoce_warzywa"
    # puszki, słoiki i mrożonki stoją w sklepie gdzie indziej niż świeże warzywa
    for pid in ("passata", "pomidory_z_puszki", "kukurydza_konserwowa", "frytki_mrozone"):
        assert shop_group(pid, p[pid].category) == "inne", pid
    assert shop_group("ryz_bialy", Category.RICE) == "inne"
    assert shop_group("makaron_pszenny", Category.PASTA) == "inne"
