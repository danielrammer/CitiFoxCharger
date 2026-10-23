"""Read-only, identity-free summary of the published charging-profile schema."""
from .skoda import timestamp


def summarize(value, errors=(), advertises_update=False):
    data = value if isinstance(value, dict) else {}
    profiles = data.get("profiles")
    code = next((suffix.lower() for suffix in ("UNSUPPORTED", "DISABLED", "UNAVAILABLE")
                 if "CHARGING_PROFILES_"+suffix in errors), None)
    valid = isinstance(profiles, list)
    code = code or ("ok" if valid else "not_returned" if value is None else "invalid")
    current = data.get("currentVehiclePositionProfile")
    current = current if isinstance(current, dict) else {}
    current_id = current.get("id")
    current_valid = type(current_id) is int
    def percent(value, minimum=0):
        return value if type(value) is int and minimum <= value <= 100 else None
    result = {"available":valid and code == "ok", "code":code,
              "advertises_update":advertises_update is True,
              "captured_at":data.get("carCapturedTimestamp") if timestamp(data.get("carCapturedTimestamp")) is not None else None,
              "profile_count":len(profiles) if valid else None,
              "current_profile_present":current_valid,
              "current_target_percent":percent(current.get("targetStateOfChargeInPercent"), 1), "profiles":[]}
    for index, profile in enumerate(profiles[:32] if valid else []):
        if not isinstance(profile, dict):
            continue
        settings = profile.get("settings")
        settings = settings if isinstance(settings, dict) else {}
        minimum = settings.get("minBatteryStateOfCharge")
        minimum = minimum if isinstance(minimum, dict) else {}
        timers = profile.get("timers")
        timers_valid = isinstance(timers, list) and all(isinstance(item, dict) and type(item.get("enabled")) is bool for item in timers)
        result["profiles"].append({"index":index+1,
            "is_current":profile["id"] == current_id if current_valid and type(profile.get("id")) is int else None,
            "target_percent":percent(settings.get("targetStateOfChargeInPercent"), 1),
            "minimum_enabled":minimum.get("enabled") if type(minimum.get("enabled")) is bool else None,
            "minimum_percent":percent(minimum.get("minimumBatteryStateOfChargeInPercent")),
            "timer_count":len(timers) if isinstance(timers, list) else None,
            "enabled_timer_count":sum(item["enabled"] for item in timers) if timers_valid else None})
    return result
