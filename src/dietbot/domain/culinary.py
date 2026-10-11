"""Hard recipe safeguards, applied to live recipes, never to historical snapshots.

These are cooking constraints, not nutrition targets. A solver must change the
meal or report infeasibility rather than pay a penalty to break these rules.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from ..textnorm import normalize
from .dish import DishTemplate, IngredientGroup, Portioning, Ratio, Role
from .product import Category, Product

VERSION = 1
BREAD_DISH = re.compile(r"^(kanapk|tost|grzank|bagiet|bulka |bulecz|wrap|tortill|pita |burger|cheeseburger)")
DELICATE = re.compile(r"ciasto|panier|masa|klops|kotlet|farsz")
NEEDS_STRUCTURE = re.compile(r"nalesnik|krokiet|plack|placus|racuch|gofr|pierog|kopytk|klusk|panier|katsu|schabow")

# Reviewed gaps in the bundled recipes. Only named members are linked; potato,
# rice, salad etc. remain separate. No guessed ingredient assignments for AI.
MISSING_GROUPS = {
    "placki_z_batatow_z_twarogiem": [("masa na placki", ["bataty", "jajko_kurze", "maka_pszenna"])],
    "placuszki_proteinowe_z_malinami": [("masa na placuszki", ["twarog_poltlusty", "jajko_kurze", "maka_pszenna"])],
    "pieczony_kurczak_w_panierce_z_ziemniakami_i_mizeria": [
        ("kotlet w panierce", ["piers_z_kurczaka", "jajko_kurze", "maka_pszenna", "bulka_tarta"]),
        ("mizeria", ["ogorek", "jogurt_naturalny_1_5"]),
    ],
    "krokiety_z_kapusta_i_pieczarkami": [("ciasto naleśnikowe", ["maka_pszenna", "mleko_1_5", "jajko_kurze"])],
}


def protect_recipe(original: DishTemplate, products: Mapping[str, Product]) -> DishTemplate:
    """Idempotent overlay: upgrades installed YAML without overwriting user's files."""
    if original.culinary_version == VERSION:
        return original
    dish = original.model_copy(deep=True)
    dish.culinary_version = VERSION
    dish.culinary_notes = []
    dish.culinary_errors = []
    by_id = {i.product_id: i for i in dish.ingredients}
    if any(p not in products for p in by_id):
        return dish  # ordinary data validation reports unknown products
    name = normalize(dish.name)
    text = normalize(" ".join(dish.steps))

    for label, members in MISSING_GROUPS.get(dish.id, []):
        if (all(p in by_id for p in members) and not (set(members) & dish.grouped_products())
                and all(not by_id[p].optional and by_id[p].scales and by_id[p].role is not Role.SPICE
                        for p in members)):
            for p in members:
                by_id[p].portioning = Portioning.SHARED
            dish.groups.append(IngredientGroup(name=label, products=members, tolerance=0.05))

    # Flour is used both for breading and sauce in these two recipes. Do not
    # falsely assign all of it to one component: link its TOTAL to the meat.
    dual_flour = {
        "japonskie_katsu_curry_z_ryzem": "piers_z_kurczaka",
        "schabowy_z_ziemniakami_i_kapusta_zasmarzana": "schab",
    }
    meat = dual_flour.get(dish.id)
    if meat and meat in by_id and "maka_pszenna" in by_id:
        by_id["maka_pszenna"].step_g = 1
        by_id["maka_pszenna"].unit = None
        ratio = by_id["maka_pszenna"].typical_g / by_id[meat].typical_g
        dish.ratios.append(Ratio(numerator=["maka_pszenna"], denominator=[meat], min=ratio * .95,
                                max=ratio * 1.05, description="Łączna mąka do panierki i sosu"))
        for group in dish.groups:
            if "panier" in normalize(group.name) and meat not in group.products:
                group.products.append(meat)
                by_id[meat].portioning = by_id[group.products[0]].portioning

    for group in dish.groups:
        if DELICATE.search(normalize(group.name)):
            group.tolerance = min(group.tolerance if group.tolerance is not None else .05, .05)
            for p in group.products:
                by_id[p].step_g = 1  # a large custom step must not become a large ratio loophole
            dish.culinary_notes.append(f"{group.name}: skalowana razem, proporcje ±5% i do 1 g zaokrąglenia.")

    # Sandwich filling must track its bread, not move in the opposite direction.
    bread = [i for i in dish.ingredients if products[i.product_id].category is Category.BREAD
             and i.product_id != "bulka_tarta" and not i.optional and i.scales]
    if bread and BREAD_DISH.search(name) and "francusk" not in name:
        bread_g = sum(i.typical_g for i in bread)
        closed_toast = name.startswith("tost") and "przykryj" in text and "krom" in text
        for ing in bread:
            if ing.product_id == "chleb_tostowy" and ing.product_id not in dish.grouped_products():
                measure = products[ing.product_id].measure("kromka")
                if measure:
                    ing.unit = None
                    ing.step_g = measure.grams * (2 if closed_toast else 1)
        cheese = [i for i in dish.ingredients if products[i.product_id].category is Category.CHEESE
                  and not i.optional and i.scales and i.typical_g >= 15]
        # A closed cheese toast needs a filling, not a dusting: 20 g per 50 g
        # bread (two reference slices). This is an explicit product recipe rule.
        if closed_toast and cheese:
            cheese_g = sum(i.typical_g for i in cheese)
            minimum = .4 * bread_g
            if cheese_g < minimum:
                for ing in cheese:
                    required = ing.typical_g * minimum / cheese_g
                    if ing.max_g is not None and required > ing.max_g:
                        dish.culinary_errors.append("Zakres sera jest za mały do pełnego tosta; popraw przepis.")
                    else:
                        ing.typical_g = required
            dish.ratios.append(Ratio(numerator=[i.product_id for i in cheese],
                                    denominator=[i.product_id for i in bread], min=.4,
                                    description="Co najmniej 20 g sera na 2 kromki tostowe (50 g pieczywa)"))
            dish.culinary_notes.append("Zamknięte tosty: pary kromek i minimum 20 g sera na 50 g pieczywa.")
        filling_categories = {Category.CHEESE, Category.DAIRY, Category.EGGS, Category.MEAT,
                              Category.POULTRY, Category.FISH, Category.COLD_CUTS, Category.LEGUMES}
        for ing in dish.ingredients:
            if (ing not in bread and not ing.optional and ing.scales and ing.typical_g >= 15
                    and (products[ing.product_id].category in filling_categories
                         or ing.product_id in {"awokado", "hummus", "maslo_orzechowe", "tofu"})):
                ratio = ing.typical_g / bread_g
                dish.ratios.append(Ratio(numerator=[ing.product_id], denominator=[i.product_id for i in bread],
                                        min=ratio * .75, max=ratio * 1.25,
                                        description="Nadzienie względem pieczywa — proporcja przepisu ±25%"))
        dish.culinary_notes.append("Pieczywo i główne nadzienie rosną razem; dodatki nie są wymieniane na puste kromki.")

    # Missing declarations in future/custom recipes fail closed instead of
    # allowing an optimizer (or an LLM) to invent a batter solely to fit macros.
    flours = {i.product_id for i in dish.ingredients if i.product_id.startswith("maka_") and not i.optional}
    if NEEDS_STRUCTURE.search(name) and flours:
        covered = dish.grouped_products()
        if meat:
            covered.add("maka_pszenna")
        if flours - covered:
            dish.culinary_errors.append("Brak chronionych proporcji ciasta/panierki dla: " + ", ".join(sorted(flours-covered)))
        elif "nalesnik" in name or "krokiet" in name:
            # Keep stuffing proportional too, not 5x the filling in half a pancake.
            batter = next((g for g in dish.groups if set(g.products) & flours), None)
            stuffing = {p for g in dish.groups if "farsz" in normalize(g.name) for p in g.products}
            fillings = [i for i in dish.ingredients if i.product_id not in (batter.products if batter else [])
                        and not i.optional and i.scales and (i.role in (Role.PROTEIN, Role.DAIRY)
                        or i.product_id in stuffing or i.product_id == "bulka_tarta")]
            flour_g = sum(by_id[p].typical_g for p in flours)
            for ing in fillings:
                ratio = ing.typical_g / flour_g
                dish.ratios.append(Ratio(numerator=[ing.product_id], denominator=sorted(flours),
                                        min=ratio * .75, max=ratio * 1.25,
                                        description="Nadzienie względem ciasta — proporcja przepisu ±25%"))
        if re.search(r"nalesnik|plack|placus|racuch|gofr", name):
            # A declared flour group alone is not enough if eggs or liquid can
            # still float independently. Ambiguous new recipes require review.
            binders = {i.product_id for i in dish.ingredients if i.product_id == "jajko_kurze"
                       or i.product_id.startswith(("mleko_", "napoj_"))}
            missing = binders - covered
            if missing:
                dish.culinary_errors.append("Niepełne proporcje masy — przypisz jajka i płyn do odpowiedniej grupy: "
                                           + ", ".join(sorted(missing)))
    return dish
