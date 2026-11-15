"""Phase C wiring of EMSController (docs/roadmap/gesamtplan.md, section 6).

- C1: ``explain`` – every reason as a sentence (reason_text.py).
- C2: overrides with an expiry (overrides.py), rank 3 of the rule order:
  applied after the decision, never over a hard limit.
- C3: ``timeline``, ``next_action`` and ``plan_inputs`` (timeline.py).
- C4: ``plan_fidelity`` – target against reality per channel and minute.
- C5: ``savings`` – headline figure against a household without PV/battery.
- C7: ``alerts`` – conditions the HA integration turns into miniems_alert
  events; warnings for a plan that is on but not deciding, and for V1
  silently clamping the charge power.

Display only, apart from C2: with no override set, no decision changes.
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from const import EMSMode
from overrides import OverrideStore
from plan_fidelity import ChannelReading, PlanJournal
from reason_text import explain_all
from savings import day_saving, summarise
from timeline import build_timeline, next_action, plan_inputs

if TYPE_CHECKING:
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

_SAVINGS_SEC = 900
_PRICE_SCAN_H = 48
_ACTION_OF_MODE = {
    EMSMode.IDLE: "auto", EMSMode.PV_CHARGING: "auto", EMSMode.EXPORT_SURPLUS: "export",
    EMSMode.GRID_CHARGING: "charge", EMSMode.HOLD_FOR_PEAK: "hold",
    EMSMode.PROTECT_BATTERY: "hold",
}


class PhaseCMixin:
    def _init_phase_c(self, overrides: OverrideStore | None, store: "EnergyStore | None") -> None:
        self._overrides = overrides if overrides is not None else OverrideStore(store)
        self._c_store = store
        self._journal = PlanJournal()
        self._price_hour: tuple[datetime, float, int] | None = None
        self._savings: dict | None = None
        self._savings_at: datetime | None = None
        self._v1_requested_w: float | None = None
        self._timeline_slots: list[dict] = []
        self._plan_inputs: dict = {}
        self._peak_price: float | None = None
        self._override_was_active = False

    @property
    def overrides(self) -> OverrideStore:
        return self._overrides

    # ------------------------------------------------------------------
    # C2 – overrides
    # ------------------------------------------------------------------

    async def _update_phase_c_pre(self, now: datetime) -> None:
        await self._overrides.load()
        ev = getattr(self, "_ev", None)
        if ev is not None:
            ev.override_mode = self._overrides.value("ev", now)

    def _apply_override(self, decision, bat_soc: float | None, now: datetime):
        """Rank 3: a manual battery override, never over a hard limit."""
        o = self._overrides.active("battery", now)
        if o is None:
            if self._override_was_active:
                # Ended (by hand or expiry): back to Auto at once, not after
                # the dwell time – the old override reason would linger.
                self._override_was_active = False
                return dataclasses.replace(decision, urgent=True)
            return decision
        self._override_was_active = True
        if decision.mode is EMSMode.PROTECT_BATTERY or decision.reason == "soc unavailable":
            return decision
        until = o.until.astimezone(now.tzinfo).strftime("%H:%M")
        reason = f"override: {o.value} until {until}"
        cls = type(decision)
        if o.value == "grid_charge":
            if bat_soc is not None and bat_soc >= self._cfg.battery_max_soc:
                return decision          # full: nothing to charge
            return cls(EMSMode.GRID_CHARGING, reason, urgent=True)
        if o.value == "hold":
            return cls(EMSMode.HOLD_FOR_PEAK, reason, urgent=True)
        if o.value == "no_grid_charge" and decision.mode is EMSMode.GRID_CHARGING:
            return cls(EMSMode.IDLE, reason, urgent=True)
        return decision

    # ------------------------------------------------------------------
    # After the decision
    # ------------------------------------------------------------------

    async def _update_phase_c_post(self, now: datetime, price: float | None, pv_w: float | None,
                                   bat_kwh_free: float | None) -> None:
        await self._record_price_hour(now, price)
        self._update_timeline_inputs(now, price, pv_w)
        if self._mode is EMSMode.GRID_CHARGING:
            self._v1_requested_w = self._grid_charge_power_w(bat_kwh_free, now)
        else:
            self._v1_requested_w = None
        self._journal.record(now, self._channel_readings(), self._all_simulated())
        if self._journal.day is not None and now.minute % 15 == 0:
            await self._journal.flush(self._c_store)
        await self._update_savings(now)

    async def _record_price_hour(self, now: datetime, price: float | None) -> None:
        hour = now.replace(minute=0, second=0, microsecond=0)
        acc = self._price_hour
        if acc is not None and acc[0] != hour and acc[2] > 0 and self._c_store is not None:
            h = acc[0]
            await self._c_store.upsert_hour(h.isoformat(), {
                "date": h.date().isoformat(), "hour": h.hour,
                "price_eur_kwh": round(acc[1] / acc[2], 5)})
        if acc is None or acc[0] != hour:
            acc = (hour, 0.0, 0)
        if price is not None:
            acc = (hour, acc[1] + price, acc[2] + 1)
        self._price_hour = acc

    def _update_timeline_inputs(self, now: datetime, price: float | None, pv_w: float | None) -> None:
        slots, status, pv_known, load_known = self._plan_slots(
            now, price, pv_w, need_pv=False, need_load=False)
        self._timeline_slots = [
            {"start": s.start.isoformat(), "end": s.end.isoformat(),
             "price_eur_kwh": None if s.rate_eur_kwh is None else round(s.rate_eur_kwh, 5),
             "pv_kwh": round(s.pv_kwh, 3), "load_kwh": round(s.load_kwh, 3)}
            for s in (slots or [])
        ]
        rates = [s.rate_eur_kwh for s in (slots or [])
                 if s.rate_eur_kwh is not None and s.start < now + timedelta(hours=24)]
        self._peak_price = max(rates) if rates else None
        price_until = None
        curve = self._price_curve()
        if curve is not None:
            cursor = now.replace(second=0, microsecond=0)
            step = timedelta(minutes=15)
            for _ in range(_PRICE_SCAN_H * 4):
                if curve.rate_at(cursor + step) is None:
                    break
                cursor += step
            price_until = cursor + step
        if self._solcast is None:
            pv = "not configured"
        elif self._solcast_data_stale():
            pv = "solcast stale – no PV in the plans"
        elif pv_known:
            pv = "solcast"
        else:
            pv = status or "missing"
        fc = self._forecaster
        if fc is None or fc.model is None:
            load = "no load model – flat share only"
        elif fc.ready:
            load = f"model ({fc.model.days} days)"
        else:
            load = f"learning ({fc.model.days} of {self._cfg.load_forecast_min_days} days)"
        self._plan_inputs = plan_inputs(price_until=price_until, pv_status=pv, load_status=load)

    def _all_simulated(self) -> bool:
        cfg = self._cfg
        live = [
            cfg.battery_control_enabled and not cfg.battery_control_simulation,
            cfg.hw_control_enabled and not cfg.hw_control_simulation,
            cfg.ev_control_enabled and not cfg.ev_control_simulation,
            cfg.heating_control_enabled and not cfg.heating_control_simulation,
        ]
        return not any(live)

    def _channel_readings(self) -> list[ChannelReading]:
        """Target and device value of every live actuator channel."""
        cfg = self._cfg
        ws = self._ws
        out: list[ChannelReading] = []
        inv = self._inverter
        if inv is not None and cfg.battery_control_enabled and not cfg.battery_control_simulation:
            for name, entity, target in (
                ("charge_current", cfg.inverter_charge_current_entity, inv.charge_current_target_a),
                ("discharge_current", cfg.battery_discharging_current_entity,
                 inv.discharge_current_target_a),
                ("grid_charge_current", cfg.inverter_grid_charge_current_entity,
                 getattr(inv, "grid_charge_current_target_a", None)),
            ):
                if entity and target is not None:
                    out.append(ChannelReading(name, entity, target, ws.get_state_value(entity), 1.0))
        hw = self._hot_water
        if (hw is not None and hw.enabled and cfg.hw_control_enabled
                and not cfg.hw_control_simulation):
            st = hw.status(datetime.now().astimezone()).get("setpoint") or {}
            if st.get("target_c") is not None:
                out.append(ChannelReading("boiler_setpoint", cfg.hw_target_entity, st["target_c"],
                                          st.get("read_back_c"), 0.5, bool(st.get("confirmed"))))
        ev = getattr(self, "_ev", None)
        if ev is not None and ev.controlling and not ev.simulation and cfg.ev_current_entity:
            cmd = ev.status(datetime.now().astimezone()).get("command") or {}
            if cmd.get("enable"):
                out.append(ChannelReading("ev_current", cfg.ev_current_entity, cmd.get("current_a"),
                                          ws.get_state_value(cfg.ev_current_entity), 0.5))
        heat = getattr(self, "_heating", None)
        if heat is not None and heat.controlling and not heat.simulation:
            out.append(ChannelReading("flow_offset", cfg.heating_offset_entity, heat.offset_k,
                                      ws.get_state_value(cfg.heating_offset_entity), 0.15))
        return out

    async def _update_savings(self, now: datetime) -> None:
        store = self._c_store
        if store is None:
            return
        if self._savings_at is not None and (now - self._savings_at).total_seconds() < _SAVINGS_SEC:
            return
        self._savings_at = now
        try:
            since = (now - timedelta(days=40)).date()
            days = await store.query_recent_days(40)
            hours = await store.query_hours_since(since)
        except Exception as exc:     # display only – never break the tick
            _LOGGER.warning("Savings not computed: %s", exc)
            return
        prices: dict[str, dict[int, float]] = {}
        for h in hours:
            if h.get("price_eur_kwh") is not None:
                prices.setdefault(h["date"], {})[int(h["hour"])] = h["price_eur_kwh"]
        if self._price_hour is not None and self._price_hour[2] > 0:
            ph = self._price_hour
            prices.setdefault(ph[0].date().isoformat(), {})[ph[0].hour] = ph[1] / ph[2]
        results = []
        for row in days:
            ds = day_saving(row, prices.get(str(row.get("date")), {}))
            if ds is not None:
                results.append(ds)
        plan = getattr(self, "_battery_plan", None)
        self._savings = summarise(results, now.date(),
                                  plan.savings_eur if plan is not None else None)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _phase_c_status(self, result: dict[str, Any], now: datetime) -> dict[str, Any]:
        cfg = self._cfg
        result = {**result,
                  "peak_price_eur": self._peak_price,
                  "battery_min_soc": cfg.battery_min_soc,
                  "timeline_slots": self._timeline_slots,
                  "plan_inputs": self._plan_inputs}
        out: dict[str, Any] = {
            "peak_price_eur": self._peak_price,
            "battery_min_soc": cfg.battery_min_soc,
            "timeline_slots": self._timeline_slots,
            "plan_inputs": self._plan_inputs,
            "overrides": self._overrides.status(now),
            "plan_fidelity": self._journal.status(now),
            "savings": self._savings,
        }
        out["explain"] = explain_all(result)
        tl = build_timeline(result)
        out["timeline"] = tl
        out["next_action"] = next_action(tl, now, _ACTION_OF_MODE.get(self._mode))
        out["alerts"] = self._alerts(result)
        return out

    def _phase_c_warnings(self) -> list[str]:
        cfg = self._cfg
        warnings = []
        if cfg.load_forecast_enabled and not self._bridge_in_control():
            warnings.append(f"Bridging plan enabled but not deciding (fallback): {self._bridge_status}")
        if self._v1_requested_w is not None:
            voltage = (self._ws.get_state_value(cfg.battery_voltage_entity)
                       if cfg.battery_voltage_entity else None) or 48.0
            max_w = cfg.battery_max_charge_current_a * voltage
            if self._v1_requested_w > max_w * 1.02:
                warnings.append(
                    f"Grid charge: {self._v1_requested_w / 1000:.1f} kW needed to finish in the "
                    f"window, only {max_w / 1000:.1f} kW possible – charging at the maximum")
        return warnings

    def _alerts(self, status: dict[str, Any]) -> list[dict[str, str]]:
        """Conditions for miniems_alert; the key changes when the condition does."""
        alerts: list[dict[str, str]] = []
        ev = status.get("ev") or {}
        ev_plan = ev.get("plan") or {}
        if ev_plan and ev_plan.get("feasible") is False:
            alerts.append({"type": "target_unreachable", "device": "ev",
                           "key": f"ev:{ev_plan.get('deadline')}", "message": ev_plan.get("reason", "")})
        hw_plan = (status.get("hot_water") or {}).get("plan") or {}
        if hw_plan and hw_plan.get("feasible") is False:
            alerts.append({"type": "target_unreachable", "device": "hot_water",
                           "key": f"hw:{hw_plan.get('need_at')}", "message": hw_plan.get("reason", "")})
        if status.get("inverter_write_status") == "error":
            stuck = ", ".join(status.get("inverter_write_stuck_channels") or [])
            alerts.append({"type": "write_unconfirmed", "device": "inverter",
                           "key": f"write:{stuck}", "message": f"unconfirmed: {stuck}"})
        energy_plan = status.get("energy_plan") or {}
        if energy_plan and energy_plan.get("feasible") is False:
            alerts.append({"type": "plan_infeasible", "device": "battery",
                           "key": f"plan:{date.today().isoformat()}",
                           "message": energy_plan.get("reason", "")})
        return alerts
