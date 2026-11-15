"""C1 – decisions in plain language (docs/roadmap/gesamtplan.md, C1).

Every decision carries a reason code (``ModeDecision.reason`` and the
reasons of hot water, EV and heating). This module turns them into one
sentence with numbers, in German and English, e.g.

    "bridge plan: 3.2 kWh by 05:00"  →
    "Netzladen: 3,2 kWh bis 05:00, weil später bis 31 ct statt jetzt 18 ct."

Unknown reasons are passed through behind the device or mode name, so a
new rule never shows an empty text. Pure: everything is passed in.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

LANGS = ("de", "en")


def _num(value: float | None, digits: int, lang: str) -> str:
    if value is None:
        return "?"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "de" else text


def _ct(eur: float | None, lang: str) -> str:
    return "?" if eur is None else f"{_num(eur * 100, 1, lang)} ct"


MODE_NAMES = {
    "Idle": ("Eigenverbrauch", "Self-use"),
    "PV Charging": ("PV lädt den Akku", "PV charges the battery"),
    "Export Surplus": ("Einspeisen", "Exporting"),
    "Grid Charging (Cheap Rate)": ("Netzladen", "Grid charging"),
    "Battery Protection (Min SoC)": ("Akkuschutz", "Battery protection"),
    "Hold for Peak (No Discharge)": ("Akku halten", "Holding the battery"),
}


def mode_name(mode: str, lang: str) -> str:
    names = MODE_NAMES.get(mode)
    if names is None:
        return mode
    return names[0] if lang == "de" else names[1]


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern
    de: Callable[[re.Match, dict], str]
    en: Callable[[re.Match, dict], str]


def _later(ctx: dict, lang: str) -> str:
    """', weil später bis 31 ct statt jetzt 18 ct' – when both prices are known."""
    peak, now = ctx.get("peak_eur"), ctx.get("price_eur")
    if peak is None or now is None or peak <= now:
        return ""
    if lang == "de":
        return f", weil später bis {_ct(peak, lang)} statt jetzt {_ct(now, lang)}"
    return f", because later up to {_ct(peak, lang)} instead of {_ct(now, lang)} now"


def _r(pattern: str, de, en) -> Rule:
    return Rule(re.compile(pattern), de, en)


def _f(m: re.Match, name: str) -> float | None:
    try:
        return float(m.group(name))
    except (TypeError, ValueError, IndexError):
        return None


# Battery / inverter mode reasons (EMSController._decide and friends).
MODE_RULES: list[Rule] = [
    _r(r"^soc unavailable$",
       lambda m, c: "Kein aktueller SoC – der Wechselrichter regelt selbst.",
       lambda m, c: "No current SoC – the inverter controls itself."),
    _r(r"^soc below min$",
       lambda m, c: f"Akku unter {c.get('min_soc', '?')} % – er wird nicht entladen.",
       lambda m, c: f"Battery below {c.get('min_soc', '?')} % – no discharge."),
    _r(r"^protect hysteresis$",
       lambda m, c: "Akkuschutz bleibt, bis der SoC wieder etwas über dem Minimum liegt.",
       lambda m, c: "Battery protection stays until the SoC is a little above the minimum."),
    _r(r"^battery full$",
       lambda m, c: "Akku voll – Überschuss geht ins Netz.",
       lambda m, c: "Battery full – surplus goes to the grid."),
    _r(r"^no action$",
       lambda m, c: "Nichts zu tun – der Akku versorgt das Haus.",
       lambda m, c: "Nothing to do – the battery supplies the house."),
    _r(r"^maintenance full charge$",
       lambda m, c: "Pflegeladung: Der Akku wird einmal voll geladen (Zellausgleich).",
       lambda m, c: "Maintenance: the battery is charged full once (cell balancing)."),
    _r(r"^negative price$",
       lambda m, c: f"Negativer Preis ({_ct(c.get('price_eur'), 'de')}) – Strombezug wird bezahlt.",
       lambda m, c: f"Negative price ({_ct(c.get('price_eur'), 'en')}) – buying is paid for."),
    _r(r"^feed-in not remunerated$",
       lambda m, c: "Einspeisung wird nicht vergütet – die PV lädt den Akku.",
       lambda m, c: "Feed-in earns nothing – PV charges the battery."),
    _r(r"^cheap rate, pv cannot fill battery$",
       lambda m, c: f"Netzladen zu {_ct(c.get('price_eur'), 'de')}: Die PV füllt den Akku heute nicht"
                    f"{_later(c, 'de')}.",
       lambda m, c: f"Grid charging at {_ct(c.get('price_eur'), 'en')}: PV will not fill the battery today"
                    f"{_later(c, 'en')}."),
    _r(r"^bridge plan: (?P<kwh>[\d.]+) kWh by (?P<until>[\d:?]+)$",
       lambda m, c: f"Netzladen: {_num(_f(m, 'kwh'), 1, 'de')} kWh bis {m.group('until')}{_later(c, 'de')}.",
       lambda m, c: f"Grid charging: {_num(_f(m, 'kwh'), 1, 'en')} kWh by {m.group('until')}{_later(c, 'en')}."),
    _r(r"^battery plan: charge (?P<kwh>[\d.]+) kWh until (?P<until>[\d:]+)$",
       lambda m, c: f"Tagesplan: {_num(_f(m, 'kwh'), 1, 'de')} kWh aus dem Netz bis {m.group('until')}"
                    f"{_later(c, 'de')}.",
       lambda m, c: f"Day plan: {_num(_f(m, 'kwh'), 1, 'en')} kWh from the grid until {m.group('until')}"
                    f"{_later(c, 'en')}."),
    _r(r"^battery plan: hold, pv charges$",
       lambda m, c: "Tagesplan: Akku halten, die PV lädt weiter.",
       lambda m, c: "Day plan: hold the battery, PV keeps charging."),
    _r(r"^battery plan: hold for later$",
       lambda m, c: f"Tagesplan: Akku für später halten{_later(c, 'de')}.",
       lambda m, c: f"Day plan: keep the battery for later{_later(c, 'en')}."),
    _r(r"^battery plan: export now, charge later$",
       lambda m, c: "Tagesplan: jetzt einspeisen, später zur PV-Spitze laden.",
       lambda m, c: "Day plan: export now, charge at the PV peak later."),
    _r(r"^battery plan: self-use$",
       lambda m, c: "Tagesplan: Eigenverbrauch.",
       lambda m, c: "Day plan: self-use."),
    _r(r"^hold for peak: (?P<need>[\d.]+) kWh needed later at >= (?P<ct>[\d.]+) ct, (?P<have>[\d.]+) kWh available$",
       lambda m, c: f"Akku halten: Später werden {_num(_f(m, 'need'), 1, 'de')} kWh zu mindestens "
                    f"{m.group('ct')} ct gebraucht, im Akku sind {_num(_f(m, 'have'), 1, 'de')} kWh.",
       lambda m, c: f"Holding the battery: {_num(_f(m, 'need'), 1, 'en')} kWh are needed later at "
                    f"≥ {m.group('ct')} ct, {_num(_f(m, 'have'), 1, 'en')} kWh are stored."),
    _r(r"^ev charging: battery does not discharge into the car \(was: (?P<was>.*)\)$",
       lambda m, c: "Das E-Auto lädt: Der Akku entlädt nicht ins Auto.",
       lambda m, c: "The EV is charging: the battery does not discharge into the car."),
    _r(r"^override: (?P<what>[a-z_]+) until (?P<until>[\d:]+)$",
       lambda m, c: f"Von Hand: {OVERRIDE_NAMES_DE.get(m.group('what'), m.group('what'))} bis {m.group('until')}.",
       lambda m, c: f"Manual: {OVERRIDE_NAMES_EN.get(m.group('what'), m.group('what'))} until {m.group('until')}."),
    # PV hold (V3a) and its releases.
    _r(r"^forecast above battery\+load need$",
       lambda m, c: "PV-Halt: Die Prognose füllt den Akku auch später – jetzt wird eingespeist.",
       lambda m, c: "PV hold: the forecast fills the battery later as well – exporting now."),
    _r(r"^forecast below battery\+load need$",
       lambda m, c: "Die PV-Prognose reicht nicht für später – die PV lädt jetzt den Akku.",
       lambda m, c: "The PV forecast is not enough for later – PV charges the battery now."),
    _r(r"^peak-time hold$",
       lambda m, c: "PV-Halt bis zur PV-Spitze – der Akku wird mittags geladen.",
       lambda m, c: "PV hold until the PV peak – the battery charges at noon."),
    _r(r"^time backstop$",
       lambda m, c: "Ab der Sicherheitsuhrzeit lädt der Akku in jedem Fall.",
       lambda m, c: "From the backstop hour on the battery always charges."),
    _r(r"^soc below export floor$",
       lambda m, c: "Akku zu leer zum Einspeisen – die PV lädt ihn.",
       lambda m, c: "Battery too low to export – PV charges it."),
    _r(r"^feed-in limit$",
       lambda m, c: "Über der Einspeisegrenze – der Akku nimmt den Überschuss.",
       lambda m, c: "Above the feed-in limit – the battery takes the surplus."),
]

OVERRIDE_NAMES_DE = {"grid_charge": "Netzladen", "hold": "Akku halten",
                     "no_grid_charge": "kein Netzladen", "now": "jetzt laden",
                     "pv": "nur PV", "stop": "Stopp"}
OVERRIDE_NAMES_EN = {"grid_charge": "grid charging", "hold": "hold the battery",
                     "no_grid_charge": "no grid charging", "now": "charge now",
                     "pv": "PV only", "stop": "stop"}

EV_RULES: list[Rule] = [
    _r(r"^not plugged in$", lambda m, c: "Nicht eingesteckt.", lambda m, c: "Not plugged in."),
    _r(r"^override: stop$", lambda m, c: "Gestoppt.", lambda m, c: "Stopped."),
    _r(r"^override: charge now(?P<rest>.*)$",
       lambda m, c: "Lädt sofort mit voller Leistung" + (" (durch die Sicherung begrenzt)." if m.group("rest") else "."),
       lambda m, c: "Charging now at full power" + (" (limited by the fuse)." if m.group("rest") else ".")),
    _r(r"^(pv only: )?pv surplus (?P<w>-?\d+) W below (?P<min>\d+) W$",
       lambda m, c: f"Wartet auf PV-Überschuss ({m.group('w')} W, nötig {m.group('min')} W).",
       lambda m, c: f"Waiting for PV surplus ({m.group('w')} W, {m.group('min')} W needed)."),
    _r(r"^(pv only: )?pv surplus (?P<w>-?\d+) W – (?P<a>\d+) A × (?P<p>\d)$",
       lambda m, c: f"Lädt mit PV-Überschuss: {m.group('a')} A, {m.group('p')}-phasig.",
       lambda m, c: f"Charging from PV surplus: {m.group('a')} A, {m.group('p')} phase(s)."),
    _r(r"^(pv only: )?pv surplus (?P<w>-?\d+) W – start (?P<p>\d)-phase$",
       lambda m, c: f"Startet mit PV-Überschuss ({m.group('w')} W), {m.group('p')}-phasig.",
       lambda m, c: f"Starting from PV surplus ({m.group('w')} W), {m.group('p')} phase(s)."),
    _r(r"^(pv only: )?pv surplus (?P<w>-?\d+) W – switch to (?P<p>\d) phases?$",
       lambda m, c: f"Schaltet auf {m.group('p')} Phase(n) um ({m.group('w')} W Überschuss).",
       lambda m, c: f"Switching to {m.group('p')} phase(s) ({m.group('w')} W surplus)."),
    _r(r"^plan: (?P<w>\d+) W in this slot \((?P<plan>.*)\)$",
       lambda m, c: f"Lädt nach Plan mit {_num(_f(m, 'w') / 1000 if _f(m, 'w') is not None else None, 1, 'de')} kW ({m.group('plan')}).",
       lambda m, c: f"Charging by plan at {_num(_f(m, 'w') / 1000 if _f(m, 'w') is not None else None, 1, 'en')} kW ({m.group('plan')})."),
    _r(r"^waiting – (?P<plan>.*)$",
       lambda m, c: f"Wartet auf das nächste geplante Fenster ({m.group('plan')}).",
       lambda m, c: f"Waiting for the next planned window ({m.group('plan')})."),
]

HEATING_RULES: list[Rule] = [
    _r(r"^price low$", lambda m, c: "Günstiger Strom – Vorlauf angehoben.",
       lambda m, c: "Cheap power – flow temperature raised."),
    _r(r"^price high$", lambda m, c: "Teurer Strom – Vorlauf abgesenkt.",
       lambda m, c: "Expensive power – flow temperature lowered."),
    _r(r"^price medium$", lambda m, c: "Normaler Preis – kein Offset.",
       lambda m, c: "Normal price – no offset."),
    _r(r"^pre-load before high price at (?P<t>[\d:]+)",
       lambda m, c: f"Vorladen vor dem teuren Preis ab {m.group('t')}.",
       lambda m, c: f"Pre-loading before the expensive price at {m.group('t')}."),
    _r(r"^outdoor (?P<t>-?[\d.]+) °C at or above the heating limit$",
       lambda m, c: f"Außen {m.group('t').replace('.', ',')} °C – über der Heizgrenze.",
       lambda m, c: f"Outdoor {m.group('t')} °C – above the heating limit."),
    _r(r"^startup: offset 0 K$", lambda m, c: "Start: Offset 0 K.", lambda m, c: "Startup: offset 0 K."),
]


def _apply(rules: list[Rule], reason: str, ctx: dict, lang: str) -> str | None:
    for rule in rules:
        m = rule.pattern.match(reason.strip())
        if m:
            return (rule.de if lang == "de" else rule.en)(m, ctx)
    return None


def explain_mode(mode: str, reason: str, ctx: dict | None = None, lang: str = "de") -> str:
    ctx = ctx or {}
    text = _apply(MODE_RULES, reason or "", ctx, lang)
    if text is not None:
        return text
    return f"{mode_name(mode, lang)}: {reason}" if reason else mode_name(mode, lang)


def explain_device(device: str, reason: str | None, ctx: dict | None = None, lang: str = "de") -> str | None:
    if not reason:
        return None
    ctx = ctx or {}
    rules = {"ev": EV_RULES, "heating": HEATING_RULES}.get(device, [])
    head = reason.split(" – ")[0]
    text = _apply(rules, reason, ctx, lang) or _apply(rules, head, ctx, lang)
    if text is not None:
        if head != reason and _apply(rules, reason, ctx, lang) is None:
            # e.g. "price high – compressor lock until 15:30"
            tail = reason[len(head) + 3:]
            text = f"{text[:-1] if text.endswith('.') else text} ({tail})."
        return text
    labels = {"ev": ("E-Auto", "EV"), "heating": ("Heizung", "Heating"),
              "hot_water": ("Warmwasser", "Hot water")}
    label = labels.get(device, (device, device))[0 if lang == "de" else 1]
    return f"{label}: {reason}"


def explain_all(status: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The 'explain' block of /api/status: one sentence per part and language."""
    classes = status.get("price_classes") or {}
    ctx = {
        "price_eur": status.get("electricity_price_eur"),
        "peak_eur": status.get("peak_price_eur") or classes.get("high_eur"),
        "min_soc": status.get("battery_min_soc"),
    }
    out: dict[str, dict[str, str]] = {
        "mode": {lang: explain_mode(status.get("mode", ""), status.get("mode_reason", ""), ctx, lang)
                 for lang in LANGS},
    }
    for device, block_key in (("hot_water", "hot_water"), ("ev", "ev"), ("heating", "heating")):
        block = status.get(block_key)
        if not isinstance(block, dict):
            continue
        reason = (block.get("command") or {}).get("reason") if device == "ev" else block.get("reason")
        texts = {lang: explain_device(device, reason, ctx, lang) for lang in LANGS}
        if any(texts.values()):
            out[device] = {k: v for k, v in texts.items() if v}
    return out
