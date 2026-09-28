"""Fresh, allowlisted charging telemetry and conditional default service."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
import re

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value, maximum, *, absolute=False):
    if type(value) not in {int, float} or not math.isfinite(value) or value == -1:
        return None
    value = abs(value) if absolute else value
    return float(value) if 0 <= value <= maximum else None


def _local_clock(value, source_zone=None):
    # Native legacy plans convert UTC; ArkUI copies plan clocks directly.
    # Absent timeZone is not proof of UTC, so preserve its reported clock.
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3])[0-5][0-9]", value):
        return None
    offset = 8 if source_zone in {"UTC", "GMT+00:00"} else 0
    return f"{(int(value[:2]) + offset) % 24:02d}:{value[2:]}"


def _schedule(charge):
    plans = charge.get("chargePlanList")
    plans = plans if isinstance(plans, list) else []
    active = [p for p in plans[:20] if isinstance(p, dict)
              and type(p.get("startSwitch")) is int and p["startSwitch"] == 1
              and p.get("isValid") != 0]
    # Multiple active plans need the App's selection policy; never pick one arbitrarily.
    if len(active) != 1:
        return {"start_clock": None, "end_clock": None, "end_enabled": None,
                "timing": "reported_clock_only", "timezone_confirmed": False,
                "timezone_status": "unreported"}
    plan = active[0]
    source_zone = plan.get("timeZone")
    source_zone = source_zone.strip() if isinstance(source_zone, str) else None
    confirmed = source_zone in {"GMT+08:00", "Asia/Shanghai", "UTC", "GMT+00:00"}
    return {"start_clock": _local_clock(plan.get("startTime"), source_zone),
            "end_clock": _local_clock(plan.get("endTime"), source_zone),
            "end_enabled": plan.get("endSwitch") == 1,
            "timing": "reported_clock_only", "timezone_confirmed": confirmed,
            "timezone_status": "confirmed" if confirmed else "unreported" if not source_zone else "unsupported"}


def charge_snapshot(data, *, now=None):
    now = now or datetime.now(SHANGHAI)
    failed = {"valid": False, "notice_valid": False, "reason": "CHARGE_TELEMETRY_REQUIRED"}
    if not isinstance(data, dict):
        return failed
    status, charge = data.get("vehicleStatus"), data.get("charge")
    if not isinstance(status, dict) or not isinstance(charge, dict):
        return failed
    if type(status.get("connectStatus")) is not int or status["connectStatus"] != 1:
        return {**failed, "reason": "VEHICLE_NOT_ONLINE"}
    timestamp = status.get("lastUpdatedAt")
    if type(timestamp) not in {int, float} or not math.isfinite(timestamp):
        return failed
    try:
        sampled = datetime.fromtimestamp(timestamp / 1000, SHANGHAI)
    except (ValueError, OverflowError, OSError):
        return failed
    if not 0 <= (now - sampled).total_seconds() <= 120:
        return {**failed, "reason": "CHARGE_TELEMETRY_STALE", "sampled_at": sampled.isoformat()}
    ac, dc = charge.get("chargeConStatus"), charge.get("dcChargeGunConnectStatus")
    if type(ac) is not int or type(dc) is not int:
        return failed
    if (ac, dc) == (1, 1):
        connected, mode = False, None
    elif (ac, dc) == (3, 1):
        connected, mode = True, "ac"
    elif (ac, dc) == (1, 2):
        connected, mode = True, "dc"
    else:
        return {**failed, "reason": "CONNECTOR_AMBIGUOUS"}
    state = charge.get("acChargeStatus") if mode == "ac" else charge.get("dcChargeStatus") if mode == "dc" else 0
    if type(state) is not int or state not in {0, 1, 5, 6, 7, 18, 25}:
        state = None
    target = _number(charge.get("maxSocPercent"), 100)
    target = int(target) if target is not None and target >= 50 and target == int(target) else None
    soc = _number(charge.get("soc"), 100)
    current = _number(charge.get("acChargeCurrent" if mode == "ac" else "dcChargeCurrent"), 1500, absolute=True)
    if current is None:
        current = _number(charge.get("chargeCurrent"), 1500, absolute=True)
    voltage = _number(charge.get("chargeVoltage"), 1500, absolute=True)
    # Non-charging frames can retain electrical values from the previous charge.
    if state != 6:
        current = voltage = None
    power = round(current * voltage / 1000, 2) if current is not None and voltage is not None else None
    valid = target is not None and soc is not None and state is not None
    return {"valid": valid, "notice_valid": True,
            "reason": None if valid else "CHARGE_FIELDS_MISSING",
            "sampled_at": sampled.isoformat(), "connected": connected, "mode": mode,
            "target": target, "soc": soc, "charging": state == 6, "charge_status": state,
            "current_a": current, "voltage_v": voltage, "power_kw": power,
            "power_source": "current_voltage_product" if power is not None else None,
            "remaining_minutes": _number(charge.get("remainChargeTime"), 10080) if state == 6 else None,
            "schedule": _schedule(charge)}


def register_service(hass):
    """Admin-only conditional default command, not an arbitrary vehicle service."""
    import voluptuous as vol
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers.service import async_register_admin_service
    from homeassistant.helpers import config_validation as cv
    from homeassistant.exceptions import HomeAssistantError
    from .const import DOMAIN
    if hass.services.has_service(DOMAIN,'apply_charge_default'): return

    async def apply(call):
        entity_id=call.data['entity_id']
        for entry in hass.data.get(DOMAIN,{}).values():
            coordinator=entry.get('coordinator') if isinstance(entry,dict) else None
            if coordinator is None: continue
            vehicle_id=coordinator.charge_limit_entities.get(entity_id)
            if vehicle_id is not None:
                return await coordinator.async_apply_charge_default(vehicle_id,
                    call.data['target'],call.data['expected_previous'],call.data['mode'],call.data['observed_at'])
        raise HomeAssistantError('charge_limit_entity_unavailable')

    async_register_admin_service(hass,DOMAIN,'apply_charge_default',apply,
        schema=vol.Schema({vol.Required('entity_id'):cv.entity_id,
            vol.Required('target'):vol.All(int,vol.In([90,95,100])),
            vol.Required('expected_previous'):vol.All(int,vol.Range(min=50,max=100)),
            vol.Required('mode'):vol.In(['ac','dc']),
            vol.Required('observed_at'):str}),supports_response=SupportsResponse.ONLY)
