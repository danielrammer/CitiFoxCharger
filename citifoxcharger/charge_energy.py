"""Charge-point energy accounting. No networking, commands or browser timers.

SOC is an estimate: the integer vehicle report, usable capacity and AC charging
losses are uncertain. A newer car capture replaces the anchor, with only the
energy measured AFTER that capture added to it. Re-reading a cloud cache does
not move the anchor. Missing local measurements are never invented.
"""
import math

CAPACITY_KWH = 32.3
EFFICIENCY = 0.90
MAX_SAMPLE_GAP = 30
MAX_CHARGING_KW = 7.4  # Citigo AC ceiling assumed for unmeasured intervals.


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def begin(soc, captured, now, power):
    return dict(anchor_soc=soc, anchor_epoch=now, vehicle_capture=captured,
                initial_report_age_seconds=max(0, now-captured),
                capacity_kwh=CAPACITY_KWH, efficiency=EFFICIENCY,
                anchor_kwh=0.0, total_kwh=0.0, estimated_soc=float(soc),
                uncertain_kwh=0.0, anchor_uncertain_kwh=0.0, upper_soc=float(soc),
                max_power_kw=max(MAX_CHARGING_KW, power),
                last_epoch=now, last_power_kw=power, history=[[now, 0.0, 0.0]],
                valid=True)


def observe(meter, captured, power, soc=None, vehicle_capture=None):
    if not meter.get('valid') or not finite(captured) or not finite(power) or not 0 <= power <= 50:
        return
    elapsed = captured-meter['last_epoch']
    if elapsed <= 0:
        return
    if elapsed > MAX_SAMPLE_GAP:
        meter['uncertain_kwh'] += meter['max_power_kw']*elapsed/3600
        meter['gap_seconds'] = meter.get('gap_seconds', 0)+elapsed
    else:
        meter['total_kwh'] += (meter['last_power_kw']+power)/2*elapsed/3600
    meter['max_power_kw'] = max(meter['max_power_kw'], power)
    meter.update(last_epoch=captured, last_power_kw=power)
    meter['history'].append([captured, meter['total_kwh'], meter['uncertain_kwh']])
    # More than a day at the default ten-second interval; retain bounded files.
    meter['history'] = meter['history'][-10000:]
    history = meter['history']
    if (type(soc) is int and 0 <= soc <= 100 and finite(vehicle_capture)
            and vehicle_capture > meter['vehicle_capture']
            and history[0][0] <= vehicle_capture <= captured):
        for left, right in zip(history, history[1:]):
            if left[0] <= vehicle_capture <= right[0]:
                fraction = (vehicle_capture-left[0])/(right[0]-left[0])
                energy = left[1]+fraction*(right[1]-left[1])
                uncertainty = left[2]+fraction*(right[2]-left[2])
                meter.update(anchor_soc=soc, anchor_epoch=vehicle_capture,
                             vehicle_capture=vehicle_capture, anchor_kwh=energy,
                             anchor_uncertain_kwh=uncertainty)
                break
    meter['estimated_soc'] = min(100.0, max(0.0, meter['anchor_soc']+
        (meter['total_kwh']-meter['anchor_kwh'])*meter['efficiency']/meter['capacity_kwh']*100))
    meter['upper_soc'] = upper(meter)


def upper(meter, now=None):
    """A modelling bound, not a BMS measurement; assume no losses for missing kWh."""
    pending = (meter['max_power_kw']*(now-meter['last_epoch'])/3600
               if now is not None and now-meter['last_epoch'] > MAX_SAMPLE_GAP else 0)
    uncertainty = meter['uncertain_kwh']-meter['anchor_uncertain_kwh']+pending
    return meter['estimated_soc']+uncertainty/meter['capacity_kwh']*100
