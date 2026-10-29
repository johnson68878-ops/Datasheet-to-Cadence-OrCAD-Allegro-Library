"""Physical package vocabulary; independent of orchestration and vendors."""
import re

PACKAGE_PATTERNS: dict[str, tuple[str, ...]] = {
    "CHIP_PASSIVE": (r"\b01005\b", r"\b0201\b", r"\b0402\b", r"\b0603\b", r"chip resistor", r"chip capacitor"),
    "QFN": (r"\b(?:UFQFPN|U?QFN|VQFN|HVQFN|TQFN)(?:[- ]?\d+)?\b", r"quad flat no[- ]lead"),
    "DFN": (r"\b(?:DFN|SON|WSON|XSON|USON)(?=\b|[-_ ]?\d)", r"dual flat no[- ]lead"),
    "BGA": (r"\b(?:BGA|FBGA|WFBGA|FCBGA|UFBGA|DSBGA|PBGA|CBGA|TFBGA|VFBGA)\b", r"(?:die size )?ball grid array"),
    "WLCSP": (
        r"\b(?:WLCSP|WCSP(?:\d+F)?|WLP|PICOSTAR)\b",
        r"\bCSP[-_ ]?\d+\b",
        r"wafer level (?:chip scale )?(?:package|packaging)",
    ),
    "LGA": (r"\bLGA\b", r"land grid array"),
    "QFP": (r"\b(?:QFP|LQFP|TQFP|MQFP|PLCC)(?=\b|[-_ ]?\d)", r"quad flat package"),
    "SOIC": (r"\b(?:SOIC|SOP|SSOP|TSSOP|MSOP|VSSOP|QSOP|SOJ)(?=\b|[-_ ]?\d)", r"small outline integrated"),
    "SOT": (r"\b(?:SOT|SC|TSOT)[- ]?\d+\b",),
    "DIODE": (r"\bSOD[- ]?\d+[A-Z]*\b", r"\bSM[ABC](?:[- ]FL)?\b", r"\bDO[- ]?\d+[A-Z]*\b"),
    "TO": (r"\bTO[- ]?\d+[A-Z]*\b", r"\bD[23]?PAK\b"),
    "THT": (r"through[- ]hole", r"\b(?:DIP|PDIP|PGA|SIP)\b", r"radial lead"),
    "CONNECTOR": (r"connector", r"header", r"receptacle", r"\bNo\.?\s*(?:of\s*)?(?:Positions|Contacts)\b", r"排针|排針|连接器|連接器"),
}


def identify_package_families(text: str) -> list[str]:
    families = []
    for family, patterns in PACKAGE_PATTERNS.items():
        flags=0 if family in {'TO','DIODE'} else re.IGNORECASE
        if any(re.search(pattern, text, flags) for pattern in patterns):
            families.append(family)
    return families
