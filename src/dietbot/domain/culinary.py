"""Hard recipe safeguards, applied to live recipes, never to historical snapshots.

These are cooking constraints, not nutrition targets. A solver must change the
meal or report infeasibility rather than pay a penalty to break these rules.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from ..textnorm import normalize
from .dish import DishTag, DishTemplate, IngredientGroup, MealType, Portioning, Ratio, Role
from .product import Category, Product

VERSION = 2
BREAD_DISH = re.compile(r"^(kanapk|tost|grzank|bagiet|bulka |bulecz|wrap|tortill|pita |burger|cheeseburger)")
DELICATE = re.compile(r"ciasto|panier|masa|klops|kotlet|farsz")
NEEDS_STRUCTURE = re.compile(r"nalesnik|krokiet|plack|placus|racuch|gofr|pierog|kopytk|klusk|panier|katsu|schabow")

# Dish coherence (version 2). Each component used to move on its own, so the solver filled a macro gap with one
# product: rice for carbs, tofu for protein, and cut coconut milk or cream for fat — a dry plate. Measured on
# 12 weeks at three calorie levels (20.10.2026): 28 of 102 sauce-dish portions had < 85% of the recipe's sauce,
# 13 < 70%; afterwards none < 70%.
# Liquids form the sauce whatever role the recipe gives them (canned tomatoes are often "warzywa").
SAUCE_LIQUIDS = frozenset({"passata", "pomidory_z_puszki", "mleczko_kokosowe", "bulion_drobiowy", "bulion_warzywny",
                           "smietana_10", "smietana_20", "smietanka_30", "sos_slodko_kwasny"})
SAUCE_DISH = re.compile(r"\bsos|tzatziki|\bdip\b|dressing")  # yogurt or cream in role "nabial" is the sauce here
SAUCE_MIN_G = 40  # a sauce item this large (2000 kcal recipe) is a real sauce, not a splash of soy sauce
MAIN_MIN_G = 30  # starch or protein smaller than this is a garnish, not a component to keep in proportion
SAUCE_PER_STARCH = .85  # sauce vs rice/pasta/groats/potatoes: at most 15% less than in the recipe
SAUCE_PER_PROTEIN = .8  # sauce vs meat/fish/tofu/legumes: at most 20% less
# No protein-vs-starch bound: with protein capped by the daily band, a higher-calorie person needs more potatoes
# for the same meat, and such a bound left the 2375 kcal test week short of carbs.

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

    # Balancing snacks skip dishes with ratios. Soups were densified on purpose (the man's plate cap filled up
    # with broth alone), so their broth does not have to grow with the meat or potatoes.
    if MealType.SNACK not in dish.meal_types and not dish.has_tag(DishTag.SOUP):
        _keep_dish_coherent(dish, products, dairy_sauce=bool(SAUCE_DISH.search(name)))

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


def _keep_dish_coherent(dish: DishTemplate, products: Mapping[str, Product], dairy_sauce: bool) -> None:
    """Sauce grows with the starch and the protein. Lower bounds only, relative to the recipe: the whole dish
    may still be smaller or larger, and the sauce may be more generous."""
    def movable(i):  # the same grams for everyone or a fixed amount would make the ratio differ per person
        p = products[i.product_id]
        return (not i.optional and i.scales and i.typical_g > 0 and i.role is not Role.SPICE and not p.negligible
                and i.portioning is not Portioning.EQUAL)

    sauce = [i for i in dish.ingredients if movable(i) and i.typical_g >= SAUCE_MIN_G
             and (i.role is Role.SAUCE or i.product_id in SAUCE_LIQUIDS
                  or (dairy_sauce and i.role is Role.DAIRY and products[i.product_id].category is Category.DAIRY))]
    in_sauce = {i.product_id for i in sauce}
    # bread on the side (shakshuka, soup) is not soaked in the sauce; more bread does not need more sauce
    starch = [i for i in dish.ingredients if movable(i) and i.role is Role.CARBS and i.product_id not in in_sauce
              and products[i.product_id].category is not Category.BREAD]
    protein = [i for i in dish.ingredients if movable(i) and i.role is Role.PROTEIN and i.product_id not in in_sauce]
    groups = {"starch": starch if sum(i.typical_g for i in starch) >= MAIN_MIN_G else [],
              "protein": protein if sum(i.typical_g for i in protein) >= MAIN_MIN_G else []}

    def names(items) -> str:
        return ", ".join(products[i.product_id].name.lower() for i in items)

    def add(num, den, share: float, why: str) -> None:
        recipe = sum(i.typical_g for i in num) / sum(i.typical_g for i in den)
        dish.ratios.append(Ratio(numerator=[i.product_id for i in num], denominator=[i.product_id for i in den],
                                 min=round(recipe * share, 4),
                                 description=f"{(products[num[0].product_id].name if len(num) == 1 else names(num)).capitalize()} "
                                             f"względem: {names(den)} — co najmniej {share:.0%} proporcji przepisu "
                                             f"({why})"))

    added = 0
    for item in sauce:
        if groups["starch"]:
            add([item], groups["starch"], SAUCE_PER_STARCH, "danie nie może wyjść suche")
            added += 1
        if groups["protein"]:
            add([item], groups["protein"], SAUCE_PER_PROTEIN, "sos do mięsa, ryby lub tofu")
            added += 1
    if added:
        dish.culinary_notes.append("Spójność dania: sos rośnie razem z ryżem, makaronem, kaszą czy ziemniakami "
                                   "i razem z mięsem, rybą lub tofu.")
