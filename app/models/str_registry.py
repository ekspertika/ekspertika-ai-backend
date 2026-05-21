from dataclasses import dataclass


@dataclass
class NormativeEntry:
    code: str
    full_name: str
    category: str  # "str" | "law" | "hn" | "other"


@dataclass
class MandatoryDocument:
    name: str
    description: str


# ---------------------------------------------------------------------------
# Full normative registry from client specification
# ---------------------------------------------------------------------------

STR_NORMATIVES: list[NormativeEntry] = [
    # LR ĮSTATYMAI
    NormativeEntry(
        "LR Statybos įstatymas",
        "LR Statybos įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Aplinkos apsaugos įstatymas",
        "LR Aplinkos apsaugos įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Saugos ir sveikatos darbe įstatymas",
        "LR Saugos ir sveikatos darbe įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Žemės įstatymas",
        "LR Žemės įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Teritorijų planavimo įstatymas",
        "LR Teritorijų planavimo įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Atliekų tvarkymo įstatymas",
        "LR Atliekų tvarkymo įstatymas",
        "law",
    ),
    NormativeEntry(
        "Statybinių atliekų tvarkymo taisyklės",
        "Statybinių atliekų tvarkymo taisyklės",
        "law",
    ),
    NormativeEntry(
        "(ES) Nr. 305/2011",
        "Europos parlamento ir tarybos reglamentas (ES) Nr. 305/2011",
        "law",
    ),
    NormativeEntry(
        "LR Specialiųjų žemės naudojimo sąlygų įstatymas",
        "LR Specialiųjų žemės naudojimo sąlygų įstatymas",
        "law",
    ),
    NormativeEntry(
        "LR Architektūros įstatymas",
        "LR Architektūros įstatymas",
        "law",
    ),
    # ORGANIZACINIAI TVARKOMIEJI STATYBOS TECHNINIAI REGLAMENTAI
    NormativeEntry(
        "STR 1.01.02:2016",
        "Normatyviniai statybos techniniai dokumentai",
        "str",
    ),
    NormativeEntry(
        "STR 1.01.03:2017",
        "Statinių klasifikavimas",
        "str",
    ),
    NormativeEntry(
        "STR 1.01.04:2015",
        "Statybos produktų, neturinčių darniųjų techninių specifikacijų, eksploatacinių savybių "
        "pastovumo vertinimas, tikrinimas ir deklaravimas",
        "str",
    ),
    NormativeEntry(
        "STR 1.01.08:2002",
        "Statinio statybos rūšys",
        "str",
    ),
    NormativeEntry(
        "STR 1.02.01:2017",
        "Statybos dalyvių atestavimo ir teisės pripažinimo tvarkos aprašas",
        "str",
    ),
    NormativeEntry(
        "STR 1.04.04:2017",
        "Statinio projektavimas, projekto ekspertizė",
        "str",
    ),
    NormativeEntry(
        "STR 1.05.01:2017",
        "Statybą leidžiantys dokumentai. Statybos užbaigimas. Statybos sustabdymas. "
        "Savavališkos statybos padarinių šalinimas",
        "str",
    ),
    NormativeEntry(
        "STR 1.06.01:2016",
        "Statybos darbai. Statinio statybos priežiūra",
        "str",
    ),
    NormativeEntry(
        "STR 1.12.06:2002",
        "Statinio naudojimo paskirtis ir gyvavimo trukmė",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(1):2005",
        "Esminiai statinio reikalavimai. Mechaninis patvarumas ir pastovumas",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(2):1999",
        "Esminiai statinio reikalavimai. Gaisrinė sauga",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(3):1999",
        "Esminiai statinio reikalavimai. Higiena, sveikata, aplinkos apsauga",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(4):2008",
        "Esminiai statinio reikalavimai. Naudojimo sauga",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(5):2008",
        "Esminiai statinio reikalavimai. Apsauga nuo triukšmo",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.01(6):2008",
        "Esminiai statinio reikalavimai. Energijos taupymas ir šilumos išsaugojimas",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.02:2016",
        "Pastatų energinio naudingumo projektavimas ir sertifikavimas",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.06:2009",
        "Statinių apsauga nuo žaibo. Išorinė statinių apsauga nuo žaibo",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.07:2003",
        "Pastatų vidaus ir išorės aplinkos apsauga nuo triukšmo",
        "str",
    ),
    NormativeEntry(
        "STR 2.01.12:2024",
        "Statybų klimatologija",
        "str",
    ),
    NormativeEntry(
        "STR 2.02.01:2004",
        "Gyvenamieji pastatai",
        "str",
    ),
    NormativeEntry(
        "STR 2.03.01:2019",
        "Statinių prieinamumas",
        "str",
    ),
    NormativeEntry(
        "STR 2.04.01:2018",
        "Pastatų atitvaros. Sienos, Stogai, Langai ir išorinės įėjimo durys",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.03:2003",
        "Statybinių konstrukcijų projektavimo pagrindai",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.04:2003",
        "Poveikiai ir apkrovos",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.05:2005",
        "Betoninių ir gelžbetoninių konstrukcijų projektavimas",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.06:2005",
        "Aliuminių konstrukcijų projektavimas",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.08:2005",
        "Plieninių konstrukcijų projektavimas. Pagrindinės nuostatos",
        "str",
    ),
    NormativeEntry(
        "STR 2.05.09:2005",
        "Mūrinių konstrukcijų projektavimas",
        "str",
    ),
    NormativeEntry(
        "STR 2.07.01:2003",
        "Vandentiekis ir nuotekų šalintuvas. Pastato inžinerinės sistemos. Lauko inžineriniai tinklai",
        "str",
    ),
    NormativeEntry(
        "STR 2.09.02:2005",
        "Šildymas, vėdinimas ir oro kondicionavimas",
        "str",
    ),
    # HIGIENOS NORMOS
    NormativeEntry(
        "HN 42:2009",
        "Gyvenamųjų ir viešojo naudojimo pastatų mikroklimatas",
        "hn",
    ),
    NormativeEntry(
        "HN 69:2003",
        "Šiluminis komfortas ir pakankama šiluminė aplinka darbo patalpose. "
        "Parametrų norminės vertės ir matavimo reikalavimai",
        "hn",
    ),
    NormativeEntry(
        "HN 98:2000",
        "Natūralus ir dirbtinis darbo vietų apšvietimas. Apšvietos ribinės vertės ir "
        "bendrieji matavimų reikalavimai",
        "hn",
    ),
    # KITI DOKUMENTAI
    NormativeEntry(
        "GSPR",
        "Gaisrinės saugos pagrindiniai reikalavimai",
        "other",
    ),
    NormativeEntry(
        "GPGST",
        "Gyvenamųjų pastatų gaisrinės saugos taisyklės",
        "other",
    ),
    NormativeEntry(
        "LST 1516:2015",
        "Statinio projektas. Bendrieji įforminimo reikalavimai",
        "other",
    ),
    NormativeEntry(
        "LST EN 17050-1:2010",
        "Atitikties įvertinimas. Tiekėjo deklaracija. Bendrieji nurodymai",
        "other",
    ),
    NormativeEntry(
        "ST 2124555837.01:2021",
        "Atitvarų šiltinimas polistireniniu putplasčiu",
        "other",
    ),
    NormativeEntry(
        "ST121895674.205.20.02:2014",
        "Fasadų įrengimo darbai. Vėdinamų fasadų su mineralinės vatos šilumos izoliacijos įrengimas",
        "other",
    ),
    NormativeEntry(
        "ISO 21542:2011",
        "Pastatų statyba. Aplinkos pritaikymo ir naudojimo reikalavimai",
        "other",
    ),
    NormativeEntry(
        "ISO 23599:2012",
        "Pagalbinės priemonės neregiams ir silpnaregiams. Taktiliniai vaikščiojamojo "
        "paviršiaus indikatoriai",
        "other",
    ),
]

# Lookup by code
_REGISTRY: dict[str, NormativeEntry] = {n.code: n for n in STR_NORMATIVES}


def get_by_code(code: str) -> NormativeEntry | None:
    return _REGISTRY.get(code)


def get_all_codes() -> list[str]:
    return list(_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Mandatory documents (16 required per client specification)
# ---------------------------------------------------------------------------

MANDATORY_DOCUMENTS: list[MandatoryDocument] = [
    MandatoryDocument(
        "Statinio projektavimo užduotis",
        "Užsakovo pateikta ir patvirtinta statinio projektavimo užduotis",
    ),
    MandatoryDocument(
        "Registravimo pažymėjimas",
        "UAB projektavimo įmonės registravimo pažymėjimas",
    ),
    MandatoryDocument(
        "PV ir PDV atestatai",
        "Projekto vadovo ir projekto dalių vadovų atestatai",
    ),
    MandatoryDocument(
        "Projektuotojo CA draudimas",
        "Projektuotojo privalomasis civilinės atsakomybės draudimas",
    ),
    MandatoryDocument(
        "Topografinė nuotrauka",
        "Topografinė nuotrauka",
    ),
    MandatoryDocument(
        "Projekto vadovo paskyrimo dokumentas",
        "Projekto vadovo paskyrimo dokumentas",
    ),
    MandatoryDocument(
        "Butų savininkų sprendimas",
        "Butų savininkų sprendimas dėl daugiabučio namo atnaujinimo (modernizavimo)",
    ),
    MandatoryDocument(
        "NTR išrašas – Statiniai",
        "Nekilnojamo turto registro centro duomenų banko išrašas – Statiniai",
    ),
    MandatoryDocument(
        "NTR išrašas – Žemės sklypas",
        "Nekilnojamo turto registro centro duomenų banko išrašas – Žemės sklypas",
    ),
    MandatoryDocument(
        "Kadastro duomenų byla",
        "Nekilnojamojo turto objekto kadastro duomenų byla",
    ),
    MandatoryDocument(
        "Įgaliojimas",
        "Įgaliojimas atstovauti užsakovą",
    ),
    MandatoryDocument(
        "Rašytiniai pritarimai",
        "Rašytiniai pritarimai (kaimynų, institucijų ir kt.)",
    ),
    MandatoryDocument(
        "Investicijų planas",
        "Namo atnaujinimo (modernizavimo) investicijų planas",
    ),
    MandatoryDocument(
        "Energinio naudingumo sertifikatas",
        "Pastato energinio naudingumo sertifikatas",
    ),
    MandatoryDocument(
        "Specialieji architektūros reikalavimai",
        "Specialieji architektūros reikalavimai",
    ),
    MandatoryDocument(
        "Licencijuotos programinės įrangos sąrašas",
        "Naudotos licencijuotos programinės įrangos sąrašas",
    ),
]
