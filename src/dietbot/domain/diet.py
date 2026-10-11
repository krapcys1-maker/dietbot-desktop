"""Conservative compatibility: unknown composite foods are not assumed plant-based."""

from collections.abc import Mapping

from .dish import DishTemplate
from .product import Category, Product

# Generic bundled foods, not declarations about brands. Bread and pasta must be egg/milk-free.
PLANT_IDS = frozenset(["bulka_pszenna", "chleb_pszenny", "chleb_zytni", "chleb_mieszany", "chleb_pelnoziarnisty", "bagietka", "ryz_bialy", "ryz_parboiled", "ryz_brazowy", "makaron_pszenny", "makaron_pelnoziarnisty", "ziemniaki", "bataty", "kasza_bulgur", "kuskus", "maka_pszenna", "maka_ziemniaczana", "platki_owsiane", "soczewica_czerwona", "ciecierzyca_konserwowa", "fasola_czerwona_konserwowa", "tofu", "pomidor", "ogorek", "papryka_czerwona", "cebula", "czosnek", "marchew", "brokul", "kalafior", "cukinia", "baklazan", "szpinak", "salata_maslowa", "salata_lodowa", "rukola", "rzodkiewka", "kapusta_biala", "kapusta_kiszona", "pieczarki", "kukurydza_konserwowa", "groszek_mrozony", "fasolka_szparagowa", "pomidory_z_puszki", "passata", "koncentrat_pomidorowy", "por", "seler_korzen", "pietruszka_korzen", "natka_pietruszki", "szczypiorek", "bazylia_swieza", "imbir_swiezy", "oliwki_zielone", "jablko", "banan", "gruszka", "pomarancza", "truskawki", "maliny", "borowki", "winogrona", "kiwi", "awokado", "sok_z_cytryny", "orzechy_wloskie", "migdaly", "orzechy_nerkowca", "orzeszki_ziemne", "maslo_orzechowe", "siemie_lniane", "pestki_dyni", "slonecznik", "nasiona_chia", "oliwa_z_oliwek", "olej_rzepakowy", "olej_slonecznikowy", "cukier", "kakao", "sos_sojowy", "mleczko_kokosowe", "ocet_balsamiczny", "kawa_napar", "woda", "sol", "pieprz_czarny", "papryka_slodka_mielona", "oregano_suszone", "curry_przyprawa", "czosnek_granulowany", "cynamon", "ziola_prowansalskie", "lisc_laurowy", "majeranek", "ogorek_kiszony", "ogorek_konserwowy", "burak", "kapusta_pekinska", "papryka_zolta", "papryka_zielona", "dynia", "ananas", "mango", "brzoskwinia", "chlebek_pita", "jarmuz", "pieczarki_konserwowe", "szparagi", "kalarepa", "dymka", "limonka_sok", "kmin_rzymski", "kminek", "kurkuma", "papryka_ostra_mielona", "chili_platki", "papryka_wedzona", "imbir_mielony", "galka_muszkatolowa", "ziele_angielskie", "tymianek", "bazylia_suszona", "garam_masala", "kolendra_mielona", "zatar", "sumak", "ocet_jablkowy", "pieczywo_chrupkie", "wafle_ryzowe",
    # uzupełnienie po audycie 09.10.2026 (S10): zioła, przyprawy i proste produkty roślinne; pieczywo, tortilla
    # i bulion — jak wyżej, w wersji bez jaj, mleka i mięsa (sprawdzaj etykietę)
    "sezam", "koperek", "kolendra", "rozmaryn", "wloszczyzna", "cukier_waniliowy", "musztarda", "ketchup", "dzem",
    "hummus", "tortilla_pszenna", "bulka_tarta", "bulka_pelnoziarnista", "bulion_warzywny", "pomidory_suszone_w_oleju"])
VEGETARIAN_IDS = frozenset(["mleko_1_5", "mleko_3_5", "jogurt_naturalny_3_5", "jogurt_naturalny_1_5", "skyr_naturalny", "kefir", "maslanka", "smietana_10", "smietana_20", "smietanka_30", "twarozek_typu_quark_chudy", "ricotta", "mascarpone", "jajko_kurze", "zoltko_jaja", "bialka_jaj", "maslo", "miod", "makaron_jajeczny"])


# Odmiana nazw diet do komunikatów („nie pasuje do diety wegańskiej”, „zgodność z dietą wegańską”).
DIET_GENITIVE_PL = {"tradycyjna": "tradycyjnej", "wege": "wegetariańskiej", "wegan": "wegańskiej"}
DIET_INSTRUMENTAL_PL = {"tradycyjna": "tradycyjną", "wege": "wegetariańską", "wegan": "wegańską"}


def product_diet(product: Product) -> str:
    if product.category in {Category.MEAT, Category.POULTRY, Category.COLD_CUTS, Category.FISH}:
        return "zwierzecy"
    if product.category in {Category.DAIRY, Category.EGGS, Category.CHEESE} and product.dietary_class == "wegan":
        return "nieznany"
    if product.dietary_class:
        return product.dietary_class
    if product.id in PLANT_IDS:
        return "wegan"
    if product.id in VEGETARIAN_IDS:
        return "wege"
    return "nieznany"


def compatible(product: Product, diet: str) -> bool:
    return diet == "tradycyjna" or product_diet(product) in ({"wegan", "wege"} if diet == "wege" else {"wegan"})


def dish_compatible(dish: DishTemplate, products: Mapping[str, Product], diet: str) -> bool:
    return all(i.product_id in products and compatible(products[i.product_id], diet) for i in dish.ingredients)
