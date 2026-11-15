"""C3 – one time axis for every plan, and the next action (gesamtplan.md, C3).

``build_timeline()`` collects what the plans intend – battery action and
SoC, grid-charge windows, hot water, EV, heating – into rows on one axis,
with price and PV forecast as the background. ``next_action()`` picks the
earliest upcoming change from those rows and words it; it feeds
``sensor.miniems_next_action`` (state = one sentence, attributes = device,
start, end), which notifications and voice output can use.

``plan_inputs()`` says what the plans were computed from: prices until when,
PV from Solcast or a fallback, load from the model or the learning phase.

Pure: works on the /api/status dict.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

DEVICE_NAMES = {
    "battery": ("Akku", "Battery"),
    "grid_charge": ("Netzladen", "Grid charging"),
    "hot_water": ("Warmwasser", "Hot water"),
    "ev": ("E-Auto", "EV"),
    "heating": ("Heizung", "Heating"),
}
ACTION_NAMES = {
    "auto": ("Eigenverbrauch", "self-use"),
    "hold": ("halten", "hold"),
    "charge": ("aus dem Netz laden", "charge from the grid"),
    "export": ("einspeisen, später laden", "export, charge later"),
    "preheat": ("vorheizen", "pre-heat"),
    "lower": ("absenken", "lower"),
}


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _merge(segments: list[dict]) -> list[dict]:
    """Join touching segments with the same label."""
    out: list[dict] = []
    for seg in sorted(segments, key=lambda s: s["start"]):
        if (out and out[-1]["label"] == seg["label"] and out[-1]["end"] == seg["start"]):
            out[-1] = {**out[-1], "end": seg["end"],
                       "energy_kwh": round((out[-1].get("energy_kwh") or 0) + (seg.get("energy_kwh") or 0), 3)}
        else:
            out.append(dict(seg))
    return out


def build_timeline(status: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, list[dict]] = {}
    soc: list[dict] = []

    bp = ((status.get("battery_plan") or {}).get("plan") or {})
    steps = bp.get("steps") or []
    if steps:
        rows["battery"] = _merge([{"start": s["start"], "end": s["end"], "label": s["action"]}
                                  for s in steps])
        soc = [{"t": s["end"], "soc_kwh": s.get("soc_kwh")} for s in steps]
        charge = [{"start": s["start"], "end": s["end"], "label": "charge",
                   "energy_kwh": s.get("grid_charge_kwh")}
                  for s in steps if (s.get("grid_charge_kwh") or 0) > 0.05]
        if charge:
            rows["grid_charge"] = _merge(charge)
    if "grid_charge" not in rows:
        windows = (status.get("energy_plan") or {}).get("windows") or []
        if windows:
            rows["grid_charge"] = [{"start": w["start"], "end": w["end"], "label": "charge",
                                    "energy_kwh": w.get("energy_kwh")} for w in windows]

    hw = ((status.get("hot_water") or {}).get("plan") or {})
    if hw.get("windows"):
        rows["hot_water"] = [{"start": w["start"], "end": w["end"], "label": "charge",
                              "energy_kwh": w.get("energy_kwh")} for w in hw["windows"]]

    ev = ((status.get("ev") or {}).get("plan") or {})
    if ev.get("slots"):
        rows["ev"] = _merge([{"start": s["start"], "end": s["end"], "label": "charge",
                              "energy_kwh": round(s.get("power_w", 0) / 1000 *
                                                  ((_dt(s["end"]) - _dt(s["start"])).total_seconds() / 3600), 3)}
                             for s in ev["slots"] if _dt(s.get("start")) and _dt(s.get("end"))])

    heat = ((status.get("heating") or {}).get("plan") or {})
    if heat.get("slots"):
        segs = []
        for s in heat["slots"]:
            start = _dt(s.get("start"))
            if start is None:
                continue
            label = "preheat" if s.get("action", 0) > 0 else "lower"
            segs.append({"start": s["start"], "end": s.get("end") or s["start"], "label": label})
        if segs:
            rows["heating"] = segs

    return {
        "rows": rows,
        "soc": soc,
        "background": status.get("timeline_slots") or [],
        "inputs": status.get("plan_inputs") or {},
    }


def next_action(timeline: dict[str, Any], now: datetime, current_battery: str | None = None
                ) -> dict[str, Any] | None:
    """The earliest upcoming change over every row, worded in de and en."""
    best: tuple[datetime, str, dict] | None = None
    for device, segs in (timeline.get("rows") or {}).items():
        for seg in segs:
            start = _dt(seg.get("start"))
            if start is None or start <= now:
                continue
            if device == "battery" and seg.get("label") == (current_battery or "auto"):
                continue
            if device == "battery" and seg.get("label") == "charge" and "grid_charge" in timeline["rows"]:
                continue   # worded by the grid_charge row
            if best is None or start < best[0]:
                best = (start, device, seg)
            break
    if best is None:
        return None
    start, device, seg = best
    end = _dt(seg.get("end"))
    return {
        "device": device,
        "action": seg.get("label"),
        "start": seg.get("start"),
        "end": seg.get("end"),
        "energy_kwh": seg.get("energy_kwh"),
        "text": {lang: _word(device, seg, start, end, lang) for lang in ("de", "en")},
    }


def _word(device: str, seg: dict, start: datetime, end: datetime | None, lang: str) -> str:
    i = 0 if lang == "de" else 1
    name = DEVICE_NAMES.get(device, (device, device))[i]
    t0 = start.strftime("%H:%M")
    t1 = end.strftime("%H:%M") if end else "?"
    kwh = seg.get("energy_kwh")
    amount = ""
    if kwh:
        num = f"{kwh:.1f}"
        amount = f" ({num.replace('.', ',') if lang == 'de' else num} kWh)"
    if device == "battery":
        action = ACTION_NAMES.get(seg.get("label"), (seg.get("label"), seg.get("label")))[i]
        return (f"{name}: ab {t0} {action}" if lang == "de" else f"{name}: {action} from {t0}")
    if device == "heating":
        action = ACTION_NAMES.get(seg.get("label"), (seg.get("label"), seg.get("label")))[i]
        return (f"{name}: {action} ab {t0}" if lang == "de" else f"{name}: {action} from {t0}")
    if lang == "de":
        return f"{name}: {t0}–{t1}{amount}"
    return f"{name}: {t0}–{t1}{amount}"


def plan_inputs(*, price_until: datetime | None, pv_status: str, load_status: str) -> dict[str, Any]:
    return {
        "prices_until": price_until.isoformat() if price_until else None,
        "pv": pv_status,
        "load": load_status,
    }
