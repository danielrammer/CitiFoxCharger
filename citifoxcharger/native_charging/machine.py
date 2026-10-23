"""One native charging session, with no I/O or implicit clock reads.

Read transition() from top to bottom: recovery, explicit Stop, effect results,
then the normal prepare/start/charge/stop sequence. HTTP acceptance is never
treated as vehicle confirmation. All times below are UTC epoch seconds.
"""
from dataclasses import dataclass, replace
from enum import Enum


class State(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    STARTING = "starting"
    CHARGING = "charging"
    STOPPING = "stopping"
    RECOVERING = "recovering"
    ATTENTION = "attention"


class Step(str, Enum):
    NONE = "none"
    OFF = "off"
    SET_LIMIT = "set_limit"
    WAIT_LIMIT = "wait_limit"
    MANUAL = "manual"
    WAIT_READY = "wait_ready"
    START = "start"
    WAIT_POWER = "wait_power"
    STOP = "stop"
    WAIT_STOP = "wait_stop"
    RESTORE = "restore"
    WAIT_RESTORE = "wait_restore"
    AUTO = "auto"


@dataclass(frozen=True)
class Session:
    state: State = State.IDLE
    step: Step = Step.NONE
    target: int = 80
    restore: int = 80
    request_id: str = ""
    vin: str = ""
    entered: float = 0
    deadline: float = 0
    limit_epoch: float = 0
    baseline: float = 0
    verified: float = 0
    start_epoch: float = 0
    stop_epoch: float = 0
    power_sample: float = 0
    ready_sample: float = 0
    power_lost_since: float = 0
    next_probe: float = 0
    probes: int = 0
    limit_outcome: str = "not_sent"
    start_outcome: str = "not_sent"
    stop_outcome: str = "not_sent"
    restore_outcome: str = "not_sent"
    result: str = ""
    result_reason: str = ""
    stop_request_id: str = ""
    cleanup_request_id: str = ""
    manual_restore_request_id: str = ""
    reason: str = "ready"
    normal_mode: str = "A+"
    recovered_state: str = ""


@dataclass(frozen=True)
class Facts:
    local_fresh: bool = False
    mode: str = ""
    power: float | None = None
    local_capture: float = 0
    switching: bool = False
    ready: bool = False
    vehicle_available: bool = False
    vehicle_capture: float = 0
    vehicle_state: str = ""
    soc: int | None = None
    limit: int | None = None
    start_allowed: bool = False


@dataclass(frozen=True)
class Decision:
    session: Session
    effect: str | None = None


def move(s, state, step, now, reason, seconds=0, **changes):
    if "result" in changes and changes["result"]:
        changes.setdefault("result_reason", reason)
    return replace(s, state=state, step=step, entered=now,
                   deadline=now+seconds if seconds else 0, reason=reason, **changes)


def stopped(f):
    return (f.local_fresh and f.mode == "OFF" and f.power is not None
            and 0 <= f.power <= .1)


def matches(s, f, target):
    return (f.vehicle_available and f.limit == target
            and f.vehicle_capture > s.baseline
            and s.limit_epoch <= f.vehicle_capture)


def begin(target, restore, request_id, vin, now):
    for value in (target, restore):
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError("A vehicle charging target must be an integer from 1 to 100%.")
    return Session(state=State.PREPARING, step=Step.OFF, target=target, restore=restore,
                   request_id=request_id, vin=vin, entered=now, reason="preparing_limit")


def retry_allowed(s, f):
    return bool(s.state == State.ATTENTION and s.reason == "power_stopped_target_unconfirmed"
        and s.verified and f.local_fresh and f.mode == "M+" and f.ready and not f.switching
        and f.power is not None and f.power <= .1 and f.vehicle_available
        and f.vehicle_capture >= s.start_epoch+30 and f.limit == s.target
        and f.vehicle_state in ("READY_FOR_CHARGING","CHARGING_INTERRUPTED") and f.start_allowed)


def transition(s, event, f, now, target=None, request_id=None):
    """Return the next session and at most one effect; never send a command here."""
    if event == "restart" and s.state not in (State.IDLE, State.ATTENTION):
        return Decision(replace(s, state=State.RECOVERING, reason="checking_previous_session",
                                recovered_state=s.recovered_state if s.state == State.RECOVERING else s.state.value))
    if event == "retry_start" and retry_allowed(s, f):
        return Decision(move(s, State.PREPARING, Step.WAIT_READY, now, "waiting_for_charger", 360,
                             request_id=request_id, ready_sample=0))
    if event == "retarget" and s.state == State.CHARGING:
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError("Invalid vehicle target.")
        if f.soc is not None and target <= f.soc:
            return Decision(move(s, State.STOPPING, Step.OFF, now, "user_stop", target=target,
                                 result="user_stopped"))
        return Decision(move(s, State.PREPARING, Step.OFF, now, "preparing_limit", target=target,
                             verified=0, result="", result_reason=""))
    if event == "stop" and s.state != State.STOPPING:
        return Decision(move(s, State.STOPPING, Step.OFF, now, "user_stop",
                             result="user_stopped", power_sample=0))
    if event == "retry_cleanup" and s.state == State.ATTENTION:
        if s.reason == "normal_mode_unconfirmed":
            return Decision(move(s, State.STOPPING, Step.AUTO, now, "retrying_cleanup"))
        return Decision(move(s, State.STOPPING, Step.OFF, now, "retrying_cleanup"))
    if event == "manual_restore" and s.state == State.ATTENTION and stopped(f):
        return Decision(move(s, State.STOPPING, Step.AUTO, now, "restore_confirmed_by_user",
                             restore_outcome="user_confirmed"))
    if event in ("accepted", "unknown", "rejected", "confirmed"):
        # The controller emits these only for the just-persisted operation.
        if s.step == Step.OFF:
            if event != "confirmed":
                if s.state == State.STOPPING and (s.start_epoch or s.start_outcome == "external"):
                    # Local failure must not suppress a best-effort vehicle Stop.
                    return Decision(replace(s, step=Step.STOP, reason="local_off_unconfirmed"))
                return Decision(move(s, State.ATTENTION, Step.OFF, now, "local_off_unconfirmed"))
            step = Step.SET_LIMIT if s.state == State.PREPARING else (
                Step.STOP if (s.start_epoch or s.start_outcome == "external") and s.stop_outcome != "confirmed"
                else Step.RESTORE if s.limit_epoch and s.restore_outcome not in ("confirmed","user_confirmed") else Step.AUTO)
            return Decision(replace(s, step=step, deadline=now+30 if step == Step.SET_LIMIT else s.deadline))
        if s.step == Step.WAIT_LIMIT:
            s = replace(s, limit_outcome=event)
            if event == "rejected":
                if s.start_epoch:
                    return Decision(move(s, State.STOPPING, Step.OFF, now, "limit_rejected", result="failed"))
                return Decision(move(s, State.STOPPING, Step.AUTO, now, "limit_rejected",
                                     result="failed", limit_epoch=0))
        elif s.step == Step.WAIT_READY and event != "confirmed":
            return Decision(move(s, State.STOPPING, Step.OFF, now, "manual_mode_unconfirmed", result="failed"))
        elif s.step == Step.WAIT_POWER:
            s = replace(s, start_outcome=event)
            if event == "rejected":
                return Decision(move(s, State.STOPPING, Step.OFF, now, "start_rejected", result="failed"))
        elif s.step == Step.WAIT_STOP:
            s = replace(s, stop_outcome=event)
            if event == "rejected":
                return Decision(move(s, State.ATTENTION, Step.WAIT_STOP, now, "vehicle_stop_rejected"))
        elif s.step == Step.WAIT_RESTORE:
            s = replace(s, restore_outcome=event)
            if event == "rejected":
                return Decision(move(s, State.ATTENTION, Step.WAIT_RESTORE, now, "restore_rejected"))
        elif s.step == Step.AUTO:
            if event != "confirmed":
                return Decision(move(s, State.ATTENTION, Step.AUTO, now, "normal_mode_unconfirmed"))
            return Decision(move(s, State.IDLE, Step.NONE, now, "session_ended"))
        return Decision(s)
    if event != "tick":
        return Decision(s)
    if s.state == State.ATTENTION and s.reason in ("power_stopped_target_unconfirmed", "local_data_unavailable"):
        if (f.local_fresh and f.vehicle_available and f.vehicle_capture >= s.start_epoch
                and f.soc is not None and f.soc >= s.target):
            return Decision(move(s, State.STOPPING, Step.OFF, now, "target_reported", result="target_reached"))
        if f.local_fresh and f.mode == "M+" and f.power is not None and f.power >= .5:
            return Decision(move(s, State.STARTING, Step.WAIT_POWER, now, "checking_charging_power", 30,
                                 power_sample=0, power_lost_since=0))
    if s.state in (State.IDLE, State.ATTENTION):
        return Decision(s)
    if s.state == State.RECOVERING:
        if not f.local_fresh:
            return Decision(move(s, State.ATTENTION, Step.NONE, now, "recovery_needs_local_data"))
        if s.recovered_state == State.STOPPING.value and stopped(f):
            # An uncertain Stop/restore is observed with its ORIGINAL deadline.
            # Rebooting must neither replay it nor reset its probe allowance.
            return Decision(replace(s, state=State.STOPPING, reason="checking_previous_session"))
        if (s.recovered_state in (State.CHARGING.value, State.STARTING.value)
                and s.verified and s.start_epoch and f.mode == "M+"
                and f.power is not None and f.power >= .5):
            return Decision(move(s, State.STARTING, Step.WAIT_POWER, now, "checking_charging_power", 30,
                                 power_sample=0))
        return Decision(move(s, State.STOPPING, Step.OFF, now, "cancelled_after_restart", result="interrupted"))
    # A changed confirmed limit removes the basis for vehicle-controlled charging.
    if (s.state in (State.STARTING, State.CHARGING) and s.verified
            and f.vehicle_available and f.vehicle_capture > s.verified
            and f.limit is not None and f.limit != s.target):
        return Decision(move(s, State.STOPPING, Step.OFF, now, "vehicle_limit_changed", result="interrupted"))
    if s.step == Step.OFF:
        return Decision(s, "off")
    if s.step == Step.SET_LIMIT:
        if not stopped(f):
            if now >= s.deadline:
                return Decision(move(s, State.ATTENTION, Step.SET_LIMIT, now, "local_stop_power_unconfirmed"))
            return Decision(replace(s, reason="waiting_for_local_stop"))
        return Decision(replace(s, step=Step.WAIT_LIMIT, limit_epoch=now,
                                baseline=f.vehicle_capture, deadline=now+180,
                                next_probe=now+15, probes=0, limit_outcome="unknown"), "set_limit")
    if s.step in (Step.WAIT_LIMIT, Step.WAIT_RESTORE):
        restoring = s.step == Step.WAIT_RESTORE
        if matches(s, f, s.restore if restoring else s.target):
            if restoring:
                return Decision(replace(s, step=Step.AUTO, restore_outcome="confirmed", reason="limit_restored"))
            return Decision(replace(s, step=Step.MANUAL, verified=f.vehicle_capture,
                                    limit_outcome="confirmed", reason="limit_confirmed"))
        if now >= s.deadline:
            if restoring:
                return Decision(move(s, State.ATTENTION, Step.WAIT_RESTORE, now, "restore_unconfirmed"))
            return Decision(move(s, State.STOPPING, Step.OFF, now, "limit_unconfirmed", result="failed"))
        if now >= s.next_probe and s.probes < 3:
            return Decision(replace(s, next_probe=now+60, probes=s.probes+1), "read_vehicle")
        return Decision(s)
    if s.step == Step.MANUAL:
        return Decision(replace(s, step=Step.WAIT_READY, deadline=now+360,
                                ready_sample=0, reason="waiting_for_charger"), "manual")
    if s.step == Step.WAIT_READY:
        if now >= s.deadline:
            return Decision(move(s, State.STOPPING, Step.OFF, now, "charger_ready_timeout", result="failed"))
        if f.switching:
            return Decision(replace(s, ready_sample=0, reason="phase_switching"))
        if not (f.local_fresh and f.mode == "M+" and f.ready):
            return Decision(replace(s, ready_sample=0, reason="waiting_for_charger"))
        if not s.ready_sample:
            return Decision(replace(s, ready_sample=f.local_capture))
        if f.local_capture >= s.ready_sample+3:
            if not f.start_allowed:
                return Decision(move(s, State.STOPPING, Step.OFF, now, "start_access_unavailable", result="failed"))
            return Decision(move(s, State.STARTING, Step.START, now, "submitting_start"))
        return Decision(s)
    if s.step == Step.START:
        return Decision(replace(s, step=Step.WAIT_POWER, start_epoch=now,
                                deadline=now+180, start_outcome="unknown", reason="waiting_for_power"), "start")
    if s.step == Step.WAIT_POWER:
        power_present = (f.local_fresh and f.mode == "M+" and not f.switching
                         and f.power is not None and f.power >= .5
                         and f.local_capture >= s.start_epoch)
        if power_present:
            if not s.power_sample:
                s = replace(s, power_sample=f.local_capture)
            elif f.local_capture >= s.power_sample+3:
                return Decision(move(s, State.CHARGING, Step.NONE, now, "charging_confirmed"))
        else:
            s = replace(s, power_sample=0)
        if now >= s.deadline:
            return Decision(move(s, State.STOPPING, Step.OFF, now, "start_power_timeout", result="failed"))
        return Decision(s)
    if s.state == State.CHARGING:
        # Native limit is authoritative. Neither stale SOC nor low quota triggers Stop.
        if (f.vehicle_available and f.vehicle_capture >= s.start_epoch
                and f.soc is not None and f.soc >= s.target):
            return Decision(move(s, State.STOPPING, Step.OFF, now, "target_reported", result="target_reached"))
        if f.local_fresh and f.power is not None and f.power <= .1 and not f.switching:
            if not s.power_lost_since:
                return Decision(replace(s, power_lost_since=now))
            if now-s.power_lost_since >= 45:
                return Decision(move(s, State.ATTENTION, Step.NONE, now, "power_stopped_target_unconfirmed"))
            return Decision(s)
        if not f.local_fresh:
            return Decision(move(s, State.ATTENTION, Step.NONE, now, "local_data_unavailable"))
        return Decision(replace(s, power_lost_since=0))
    if s.step == Step.STOP:
        return Decision(replace(s, step=Step.WAIT_STOP, stop_epoch=now, deadline=now+180,
                                stop_outcome="unknown", next_probe=now+15, probes=0), "stop")
    if s.step == Step.WAIT_STOP:
        confirmed = (stopped(f) and f.vehicle_available and f.vehicle_capture >= s.stop_epoch
                     and f.vehicle_state in ("READY_FOR_CHARGING", "CHARGING_INTERRUPTED", "CONNECT_CABLE", "CONSERVING"))
        if confirmed:
            return Decision(replace(s, step=Step.RESTORE if s.limit_epoch else Step.AUTO,
                                    stop_outcome="confirmed", reason="stop_confirmed"))
        if now >= s.deadline:
            return Decision(move(s, State.ATTENTION, Step.WAIT_STOP, now, "vehicle_stop_unconfirmed"))
        if now >= s.next_probe and s.probes < 3:
            return Decision(replace(s, next_probe=now+60, probes=s.probes+1), "read_vehicle")
        return Decision(s)
    if s.step == Step.RESTORE:
        if not stopped(f):
            return Decision(move(s, State.ATTENTION, Step.RESTORE, now, "local_stop_power_unconfirmed"))
        return Decision(replace(s, step=Step.WAIT_RESTORE, limit_epoch=now,
                                baseline=f.vehicle_capture, deadline=now+180, next_probe=now+15,
                                probes=0, restore_outcome="unknown", reason="restoring_limit"), "restore")
    if s.step == Step.AUTO:
        if not (f.local_fresh and f.mode in ("OFF",s.normal_mode)
                and f.power is not None and f.power <= .1):
            return Decision(move(s, State.ATTENTION, Step.AUTO, now, "normal_mode_unconfirmed"))
        return Decision(s, "normal")
    return Decision(s)
