"""Guard the rebrand against losing installation identity or offline assets."""
import json
from pathlib import Path

from dietbot import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_product_versions_and_stable_install_identity():
    installer = (ROOT / "packaging/DietBot.iss").read_text(encoding="utf-8-sig")
    assert f'#define MyAppVersion "{__version__}"' in installer
    assert '#define MyAppName "Porcja"' in installer
    assert "A62F37F9-C1A4-4C98-AB77-E99B18BFE539" in installer
    assert r"DefaultDirName={localappdata}\Programs\DietBot" in installer
    assert '#define MyAppExeName "DietBot.exe"' in installer
    for name in ("package.json", "package-lock.json"):
        assert json.loads((ROOT / "web" / name).read_text())["version"] == __version__
    version = (ROOT / "packaging/version_info.txt").read_text(encoding="utf-8")
    assert f"StringStruct('ProductVersion', '{__version__}')" in version


def test_visual_assets_are_local_and_licensed():
    main = (ROOT / "web/src/main.tsx").read_text(encoding="utf-8")
    assert "@fontsource-variable/dm-sans/wght.css" in main
    assert "@fontsource-variable/fraunces/wght.css" in main
    for name in ("lucide.txt", "dm-sans.txt", "fraunces.txt"):
        license_text = (ROOT / "web/public/licenses" / name).read_text(encoding="utf-8")
        assert "Copyright" in license_text
        assert len(license_text) > 1000
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert "Porcja" in index and 'href="/porcja.svg"' in index
    assert "https://" not in index


def test_package_ships_starter_data_without_raw_source_tables():
    spec = (ROOT / "packaging/DietBot.spec").read_text(encoding="utf-8")
    # cały folder dane zawierałby też dane\raw (surowe BLS/CIQUAL, ok. 34 MB) — do paczki tylko dane startowe
    assert r'r"\dane", r"defaults\dane"' not in spec
    assert 'for sub in ("dania", "produkty", "sklepy")' in spec
    tracked = {p.name for p in (ROOT / "dane").iterdir() if p.is_dir()} - {"raw"}
    assert tracked == {"dania", "produkty", "sklepy"}  # nowy folder danych trzeba dopisać do paczki
