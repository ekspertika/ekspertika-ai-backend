"""Load compliance.config.json into flat list of CheckItem.

Port of nextjs-fe/lib/compliance-config/loader.ts. Keep keyword tables in sync with FE
until both sides read from a shared source (planned: vault/projects/.../compliance.config.json).
"""

import json
import re
from pathlib import Path

from app.models.check_item import CheckItem

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "compliance.config.json"
_cached: list[CheckItem] | None = None


_REQUIREMENT_TEXTS: dict[str, dict] = {
    "STR 1.01.02": {
        "title": "Statybos techninis reglamentavimas",
        "requirement": (
            "Statybos techniniai reglamentai privalomi visiems statybos dalyviams. "
            "Projektas turi būti parengtas laikantis galiojančių STR reikalavimų."
        ),
        "keywords": ["reglamentas", "projektas", "statybos dalyvis", "privalomas"],
    },
    "STR 1.01.03": {
        "title": "Statinių klasifikavimas pagal jų naudojimo paskirtį",
        "requirement": (
            "Statinys turi būti priskirtas atitinkamai paskirties klasei. "
            "Projekto dokumentacijoje turi būti nurodyta paskirtis ir klasė."
        ),
        "keywords": ["paskirtis", "klasifikavimas", "kategorija", "naudojimas"],
    },
    "STR 1.01.04": {
        "title": "Statinio projektavimo normos",
        "requirement": (
            "Projektavimo normos nustato privalomuosius ir rekomenduojamuosius reikalavimus. "
            "Projektas turi atitikti visus privalomuosius norminius rodiklius."
        ),
        "keywords": ["normos", "projektavimas", "rodikliai", "reikalavimai"],
    },
    "STR 1.02.01": {
        "title": "Statybos techninė priežiūra",
        "requirement": (
            "Statybos techninė priežiūra atliekama per visą statybos laikotarpį. "
            "Projekto dokumentacija turi numatyti techninės priežiūros tvarką."
        ),
        "keywords": ["techninė priežiūra", "priežiūra", "statybos laikotarpis"],
    },
    "STR 1.04.04": {
        "title": "Statinio projektavimas, projekto ekspertizė",
        "requirement": (
            "Statinio projektas turi apimti sklypo planą, fasadus, pjūvius, planus, "
            "inžinerinių sistemų schemas ir skaičiavimus. Projektą turi pasirašyti atestuoti specialistai."
        ),
        "keywords": ["projektas", "planas", "fasadas", "pjūvis", "atestuotas", "ekspertizė", "dokumentacija"],
    },
    "STR 1.05.01": {
        "title": "Statinio projekto apimtis ir sudėtis",
        "requirement": (
            "Projekto sudėtis priklauso nuo statinio sudėtingumo kategorijos. "
            "Turi būti parengti visi privalomi projekto dalys: architektūrinė, konstrukcijų, inžinerinių sistemų."
        ),
        "keywords": ["projekto sudėtis", "dalys", "architektūra", "konstrukcijos", "inžinerinės sistemos"],
    },
    "STR 1.06.01": {
        "title": "Statybos rangos sutarčių sudarymas",
        "requirement": (
            "Statybos darbai atliekami pagal su rangovu sudarytą sutartį. "
            "Projekto dokumentai yra sutarties priedas."
        ),
        "keywords": ["rangovas", "sutartis", "rangos", "darbai"],
    },
    "STR 2.01.01": {
        "title": "Esminiai statinio reikalavimai",
        "requirement": (
            "Statinys turi būti suprojektuotas ir pastatytas taip, kad esant gaisrui konstrukcijų "
            "laikančioji galia būtų išlaikoma, gaisro ir dūmų plitimas būtų ribojamas, "
            "o ugniagesiams būtų sudarytos saugios darbo sąlygos."
        ),
        "keywords": ["gaisras", "evakuacija", "priešgaisrinė", "dūmai", "liepsna", "laikančioji", "atsparumas"],
    },
    "STR 2.01.02": {
        "title": "Statinių naudojimo paskirtys",
        "requirement": (
            "Statinio paskirtis turi atitikti teritorijų planavimo dokumentuose numatytą žemės naudojimo paskirtį. "
            "Keitimas galimas tik gavus reikiamus leidimus."
        ),
        "keywords": ["paskirtis", "teritorijų planavimas", "keitimas", "leidimas"],
    },
    "STR 2.01.06": {
        "title": "Statinio inžinerinės sistemos",
        "requirement": (
            "Inžinerinės sistemos turi užtikrinti statinio funkcionalumą ir saugumą. "
            "Sistemos projektuojamos pagal atitinkamus STR ir Eurokodinių standartų reikalavimus."
        ),
        "keywords": ["inžinerinės sistemos", "šildymas", "vėdinimas", "elektra", "vandentiekis"],
    },
    "STR 2.01.07": {
        "title": "Triukšmo apsauga",
        "requirement": (
            "Gyvenamosios patalpos turi būti apsaugotos nuo triukšmo. Pertvara tarp butų turi turėti ne mažiau "
            "kaip 52 dB oro garso izoliaciją. Perdangos – ne mažiau kaip 58 dB oro ir 50 dB smūgio garso izoliacijos."
        ),
        "keywords": ["triukšmas", "garso izoliacija", "akustika", "dB", "perdanga", "siena"],
    },
    "STR 2.01.12": {
        "title": "Statybų klimatologija",
        "requirement": (
            "Projektavimo skaičiavimuose turi būti naudojami klimatologiniai duomenys: "
            "išorės oro temperatūra, sniego apkrovos rajonas, vėjo greitis ir slėgis, "
            "grunto įšalo gylis, saulės spinduliuotė. STR 2.01.12:2024 pateikia šias "
            "reikšmes pagal Lietuvos teritorijos rajoną."
        ),
        "keywords": [
            "klimatologija",
            "sniego apkrova",
            "vėjo greitis",
            "lauko temperatūra",
            "įšalo gylis",
            "rajonas",
        ],
    },
    "STR 2.02.01": {
        "title": "Gyvenamieji pastatai",
        "requirement": (
            "Gyvenamieji pastatai turi atitikti gyvenimo kokybės, sveikatos, saugos ir komforto reikalavimus. "
            "Butų minimalūs plotai, lubų aukščiai ir natūrali šviesa turi atitikti normatyvus."
        ),
        "keywords": ["gyvenamasis", "butas", "plotas", "lubų aukštis", "šviesa", "komfortas"],
    },
    "STR 2.03.01": {
        "title": "Statiniai ir teritorijos. Reikalavimai žmonėms su negalia",
        "requirement": (
            "Visuomeniniai ir gyvenamieji daugiabučiai turi būti prieinami neįgaliesiems. "
            "Pandusai ≤ 1:12 nuolydžio, liftai min. 1,1×1,4 m, pritaikyti tualetai ir taktilinis žymėjimas."
        ),
        "keywords": ["neįgalieji", "pandusas", "liftas", "prieinamumas", "taktilinė", "vežimėlis"],
    },
    "STR 2.04.01": {
        "title": "Požeminiai inžineriniai tinklai",
        "requirement": (
            "Požeminiai tinklai projektuojami atsižvelgiant į esamas komunikacijas. "
            "Turi būti parengtas inžinerinių tinklų planas su koordinatėmis ir gyliais."
        ),
        "keywords": ["požeminis", "inžineriniai tinklai", "komunikacijos", "vamzdynas", "gyliai"],
    },
    "STR 2.05.03": {
        "title": "Geotechninis projektavimas",
        "requirement": (
            "Pagrindų ir pamatų projektavimas atliekamas remiantis geotechniniais tyrimais. "
            "Turi būti pateikta grunto skaičiuojamoji galia ir pamatų sprendinys."
        ),
        "keywords": ["pamatas", "gruntas", "geotechnika", "tyrimai", "laikančioji galia"],
    },
    "STR 2.05.04": {
        "title": "Poveikiai ir apkrovos",
        "requirement": (
            "Konstrukcijos projektuojamos atsižvelgiant į pastovias, kintamas ir atsitiktines apkrovas. "
            "Projekto dokumentacijoje turi būti pateikti apkrovų skaičiavimai."
        ),
        "keywords": ["apkrova", "konstrukcija", "svoris", "sniegas", "vėjas", "skaičiavimai"],
    },
    "STR 2.05.05": {
        "title": "Betoninės ir gelžbetoninės konstrukcijos",
        "requirement": (
            "Betoninės ir gelžbetoninės konstrukcijos projektuojamos pagal Eurokodo 2 reikalavimus. "
            "Armavimo detalės ir betono klasė turi būti nurodyti projekto brėžiniuose."
        ),
        "keywords": ["betonas", "gelžbetonas", "armavimas", "eurokodas", "C20", "C25"],
    },
    "STR 2.05.06": {
        "title": "Medinės konstrukcijos",
        "requirement": (
            "Medinės konstrukcijos projektuojamos pagal Eurokodo 5 reikalavimus. "
            "Medienos klasė, jungčių tipai ir apsauga nuo puvimo turi būti nurodyti projekto dokumentacijoje."
        ),
        "keywords": ["mediena", "medinė konstrukcija", "eurokodas 5", "jungtis", "puvimas"],
    },
    "STR 2.05.08": {
        "title": "Plieninės konstrukcijos",
        "requirement": (
            "Plieninės konstrukcijos projektuojamos pagal Eurokodo 3 reikalavimus. "
            "Antikorozinė apsauga, sujungimų tipai ir plieno klasė turi būti nurodyti projekto dokumentacijoje."
        ),
        "keywords": ["plienas", "metalinė konstrukcija", "antikorozinė", "suvirinimas", "varžtai"],
    },
    "STR 2.05.09": {
        "title": "Mūrinės konstrukcijos",
        "requirement": (
            "Mūrinės konstrukcijos projektuojamos pagal Eurokodo 6 reikalavimus. "
            "Mūro klasė, rišamosios medžiagos ir armavimo sprendiniai turi būti nurodyti projekte."
        ),
        "keywords": ["mūras", "plyta", "blokas", "eurokodas 6", "skiedinys", "armavimas"],
    },
    "STR 2.07.01": {
        "title": "Vandentiekis ir nuotekų šalintuvas",
        "requirement": (
            "Statiniai aprūpinami šalto ir karšto vandens sistemomis. Nuotekų sistema užtikrina sanitarinę apsaugą. "
            "Vamzdžių skersmuo parenkamas pagal suvartojimo skaičiavimus."
        ),
        "keywords": ["vandentiekis", "nuotekos", "kanalizacija", "karštas vanduo", "vamzdynas"],
    },
    "STR 2.09.02": {
        "title": "Šildymas, vėdinimas ir oro kondicionavimas",
        "requirement": (
            "Šildymo, vėdinimo ir oro kondicionavimo sistemos užtikrina norminę patalpų temperatūrą. "
            "Vėdinimo sistemos turi užtikrinti ne mažiau kaip 0,5 oro kartotinumo per valandą."
        ),
        "keywords": ["šildymas", "vėdinimas", "kondicionavimas", "temperatūra", "oro kartotinumas", "HVAC"],
    },
}

_LAW_KEYWORDS: dict[str, list[str]] = {
    "LR Statybos įstatymas": ["statybos leidimas", "statybos įstatymas", "statybos dalyviai", "statybos priežiūra"],
    "LR Aplinkos apsaugos įstatymas": ["aplinkosauga", "poveikio aplinkai", "ekologija", "aplinkos apsauga"],
    "LR Saugos ir sveikatos darbe įstatymas": ["sauga darbe", "sveikata darbe", "darbuotojų sauga", "rizika"],
    "LR Žemės įstatymas": ["žemė", "sklypas", "žemės naudojimas", "nuosavybė"],
    "LR Teritorijų planavimo įstatymas": ["teritorijų planavimas", "bendrasis planas", "detaliojo plano", "plano keitimas"],
    "LR Atliekų tvarkymo įstatymas": ["atliekos", "atliekų tvarkymas", "šiukšlės", "perdirbimas"],
    "Statybinių atliekų tvarkymo taisyklės": ["statybinės atliekos", "atliekų sutvarkymas", "griovimo atliekos"],
    "ES Reglamentas Nr. 305/2011": ["statybos produktai", "CE ženklinimas", "darnieji standartai", "eksploatacinės charakteristikos"],
    "LR Specialiųjų žemės naudojimo sąlygų įstatymas": ["specialiosios sąlygos", "apsaugos zonos", "apribojimai", "sanitarinė apsaugos zona"],
    "LR Architektūros įstatymas": ["architektūra", "architektūrinis sprendinys", "architektūros kokybė", "urbanistika"],
}

_STANDARD_KEYWORDS: dict[str, list[str]] = {
    "HN 42:2009": ["triukšmas", "garso lygis", "dB", "aplinkos triukšmas", "triukšmo norma"],
    "HN 69:2003": ["natūrali šviesa", "apšvietimas", "saulės šviesa", "langai", "apšvietimo koeficientas"],
    "HN 98:2000": ["vibracija", "vibracijos norma", "pastatų vibracija"],
    "GSPR": ["gaisrinė sauga", "priešgaisrinė", "evakuacija", "gaisro gesinimas"],
    "GPGST": ["gaisrinė sauga", "priešgaisrinė apsauga", "sprinkleriai"],
    "LST 1516:2015": ["prieinamumas", "neįgalieji", "pandusas", "aplinkos formavimas"],
    "LST EN 17050-1:2010": ["atitikties deklaracija", "savideklaracija", "gamintojas"],
    "ISO 21542:2011": ["prieinamumas", "neįgalieji", "universalus dizainas", "statinys"],
    "ISO 23599:2012": ["taktilinis žymėjimas", "neregiai", "orientacinis žymėjimas"],
}

_TECHNICAL_RULE_KEYWORDS: dict[str, list[str]] = {
    "ST 2124555837.01:2021": ["statybos taisyklės", "techninės taisyklės", "statybos technika"],
    "ST121895674.205.20.02:2014": ["inžineriniai tinklai", "požeminiai tinklai", "komunikacijos"],
    "statybostaisykles.lt": ["statybos taisyklės", "normos", "reikalavimai"],
}

_DOCUMENT_KEYWORDS: dict[str, list[str]] = {
    "Projektavimo užduotis": ["projektavimo užduotis", "užduotis", "techninis projektas"],
    "Įmonės registravimo pažymėjimas": ["registravimo pažymėjimas", "įmonė", "juridinis asmuo"],
    "PV ir PDV atestatai": ["atestatas", "PV atestatas", "PDV", "projekto vadovas"],
    "Civilinės atsakomybės draudimas": ["draudimas", "civilinė atsakomybė", "draudimo polisas"],
    "Topografinė nuotrauka": ["topografinė", "topografija", "situacijos planas"],
    "Projekto vadovo paskyrimo dokumentas": ["projekto vadovas", "paskyrimas", "vadovas"],
    "Gyventojų sprendimas": ["gyventojų sprendimas", "susirinkimas", "balsavimas", "pritarimas"],
    "NT registro išrašas – statiniai": ["NT registro", "nekilnojamojo turto registro", "išrašas", "statinys"],
    "NT registro išrašas – sklypas": ["NT registro", "sklypas", "žemės sklypo", "registro išrašas"],
    "Kadastro byla": ["kadastro", "kadastriniai", "matavimo byla", "kadastro byla"],
    "Įgaliojimas": ["įgaliojimas", "įgaliotinis", "atstovas"],
    "Rašytiniai pritarimai": ["pritarimas", "rašytinis pritarimas", "susijusios institucijos"],
    "Investicijų planas": ["investicijų planas", "investicijos", "finansavimas"],
    "Energijos sertifikatas": ["energijos sertifikatas", "energinio naudingumo", "energetinis"],
    "Architektūros reikalavimai": ["architektūros reikalavimai", "architektūriniai", "architektūros sąlygos"],
    "Programinės įrangos sąrašas": ["programinė įranga", "software", "projektavimo programa", "BIM"],
}

_STR_CATEGORY_LABELS: dict[str, str] = {
    "administrative": "Administrative",
    "essential_requirements": "Essential Requirements",
    "performance": "Performance",
    "buildings": "Buildings",
    "structures": "Structures",
    "engineering_systems": "Engineering Systems",
}


def _str_lookup_code(code: str) -> str:
    """Strip version + variant suffix: 'STR 2.01.01(1):2005' → 'STR 2.01.01'."""
    base = re.sub(r"\(?\d+\)?:\d+$", "", code).strip()
    return re.sub(r"\(\d+\)$", "", base).strip()


def load_all_check_items() -> list[CheckItem]:
    """Read compliance.config.json and emit a flat CheckItem list (laws + STR + standards + docs)."""
    global _cached
    if _cached is not None:
        return _cached

    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    items: list[CheckItem] = []

    for law in raw.get("laws", []):
        items.append(
            CheckItem(
                code=law,
                title=law,
                category="Laws",
                check_type="law",
                keywords=_LAW_KEYWORDS.get(law, [law.lower()]),
            )
        )

    for category_key, codes in raw.get("str", {}).items():
        category = _STR_CATEGORY_LABELS.get(category_key, category_key)
        for code in codes:
            lookup = _str_lookup_code(code)
            known = _REQUIREMENT_TEXTS.get(lookup)
            items.append(
                CheckItem(
                    code=code,
                    title=known["title"] if known else lookup,
                    category=category,
                    check_type="str",
                    requirement_text=known["requirement"] if known else None,
                    keywords=known["keywords"] if known else [lookup, "statybos", "reikalavimai"],
                )
            )

    for std in raw.get("standards", []):
        items.append(
            CheckItem(
                code=std,
                title=std,
                category="Standards",
                check_type="standard",
                keywords=_STANDARD_KEYWORDS.get(std, [std.lower()]),
            )
        )

    for rule in raw.get("technical_rules", []):
        items.append(
            CheckItem(
                code=rule,
                title=rule,
                category="Technical Rules",
                check_type="standard",
                keywords=_TECHNICAL_RULE_KEYWORDS.get(rule, [rule.lower()]),
            )
        )

    for doc in raw.get("required_documents", []):
        items.append(
            CheckItem(
                code=doc,
                title=doc,
                category="Required Documents",
                check_type="document",
                keywords=_DOCUMENT_KEYWORDS.get(doc, [doc.lower()]),
            )
        )

    _cached = items
    return items


def get_check_item_by_code(code: str) -> CheckItem | None:
    return next((item for item in load_all_check_items() if item.code == code), None)
