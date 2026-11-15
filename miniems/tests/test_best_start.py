"""B8 – best start for flexible appliances."""
from datetime import datetime, timedelta, timezone

from best_start import best_start, parse_devices, slugify
from energy_task import TaskSlot

TZ = timezone(timedelta(hours=1))
T0 = datetime(2026, 11, 16, 8, 0, tzinfo=TZ)


def slots(prices, pv=None):
    out = []
    for i, p in enumerate(prices):
        s = T0 + timedelta(minutes=30 * i)
        out.append(TaskSlot(s, s + timedelta(minutes=30), p,
                            pv_surplus_w=(pv[i] if pv else 0.0), feed_in_eur_kwh=0.08))
    return out


def test_parse_devices():
    devs = parse_devices("Waschmaschine:1.2:2, Spülmaschine:1.0:3, kaputt, Trockner:0:1")
    assert [(d.name, d.energy_kwh, d.duration_h) for d in devs] == [
        ("Waschmaschine", 1.2, 2.0), ("Spülmaschine", 1.0, 3.0)]
    assert slugify("Spülmaschine Küche") == "spulmaschine_kuche"


def test_cheapest_window():
    sl = slots([0.34, 0.34, 0.27, 0.27, 0.27, 0.27, 0.34, 0.34])
    dev = parse_devices("WM:1:2")[0]
    advice = best_start(dev, sl, T0)
    assert advice.start == sl[2].start
    assert advice.cost_eur == 0.27
    assert round(advice.saving_eur, 3) == 0.035   # now: 0.5 h each at 34, 34, 27, 27 ct


def test_pv_surplus_wins():
    sl = slots([0.27] * 8, pv=[0, 0, 0, 0, 3000, 3000, 0, 0])
    advice = best_start(parse_devices("WM:1:1")[0], sl, T0)
    assert advice.start == sl[4].start
    assert round(advice.cost_eur, 3) == 0.08


def test_run_longer_than_horizon():
    assert best_start(parse_devices("WM:1:6")[0], slots([0.3] * 4), T0) is None
