"""Allowlisted diagnostics shared by the CLI and authenticated web view."""
import math
import re
from . import __version__
from .charging import ACTIVE
from .modbus import utcnow
from .skoda import timestamp,CHARGE_MODES

def selected(data, numbers=(), flags=(), dates=(), enums=None):
    data = data if isinstance(data, dict) else {}
    result = {}
    for name in numbers:
        value = data.get(name)
        result[name] = value if type(value) in (int, float) and math.isfinite(value) else None
    for name in flags:
        result[name] = data.get(name) if type(data.get(name)) is bool else None
    for name in dates:
        result[name] = data.get(name) if timestamp(data.get(name)) is not None else None
    for name, choices in (enums or {}).items():
        value = data.get(name)
        result[name] = value if isinstance(value, str) and value in choices else None
    return result


def report(status, live=False):
    status = status if isinstance(status, dict) else {}
    car = status.get('skoda', {})
    smartfox = status.get('smartfox', {})
    charger = smartfox.get('charger', {}) if isinstance(smartfox, dict) else {}
    session = status.get('charging', {})
    command = status.get('last_command') or {}
    remaining = car.get('quota_remaining') if isinstance(car, dict) else None
    quota = int(remaining) if isinstance(remaining, str) and remaining.isascii() and remaining.isdecimal() and len(remaining) <= 6 else None
    settings = car.get('charging_settings',{}) if isinstance(car,dict) else {}
    settings = settings if isinstance(settings,dict) else {}
    modes = settings.get('available_modes')
    modes = [mode for mode in CHARGE_MODES if mode in modes] if isinstance(modes,list) else []
    version = status.get('backend_version')
    limit_test = status.get('limit_test',{})
    limit_test = limit_test if isinstance(limit_test,dict) else {}
    attempts = limit_test.get('attempts')
    test_report = {**selected(limit_test,flags=('active',)), 'attempts':[
        selected(item,numbers=('target_percent','http_status','reported_limit','quota_remaining','cooldown_seconds'),
                 dates=('requested_at','verified_at'), enums={
                     'outcome':('accepted','rejected','unknown'),
                     'verification':('not_returned','awaiting_new_capture','confirmed','different'),
                     'code':('network','auth','forbidden','unsupported','not_found','rate_limit','quota_reserved','protocol','upstream'),
                     'problem_type':('api-key-expired','api-key-not-authorized','operation-not-authorized','operation-not-supported','operation-disabled','rate-limit-exceeded','vehicle-not-accepting-requests')})
        for item in attempts[-10:]] if isinstance(attempts,list) else []}
    history = []
    prior = session.get('previous_sessions', [])
    for item in prior[-10:] if isinstance(prior, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('session'), dict):
            continue
        # Strip nested histories before applying the normal allowlist again.
        saved = {key: value for key, value in item['session'].items() if key != 'previous_sessions'}
        saved['energy_meter'] = item.get('energy_estimate', {})
        sanitized = report({'charging': saved})
        history.append({'session':sanitized['session'], 'energy_estimate':sanitized['energy_estimate']})
    return {'report_version': 1, 'program_version': __version__,
            'backend_version': version if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+', version) else None,
            'generated_at': utcnow(), 'previous_sessions':history,
            'source': 'running_backend' if live else 'saved_files_only',
            'limit_test':test_report,
            'limit_trial':selected(status.get('limit_trial',{}),
                numbers=('target_percent','initial_soc','start_http_status','stop_http_status','runtime_limit_seconds'),
                flags=('active',),
                dates=('created_at','start_at','power_confirmed_at','power_stopped_at','stop_at','finished_at',
                       'phase_started_at','phase_ended_at','power_resumed_at'),
                enums={'phase':('waiting_ready','observing','waiting_resume','stopping','finished'),
                       'evaluation':('pending','external_power_drop_observed','inconclusive_app_stop'),
                       'start_outcome':('accepted','unknown','rejected'),
                       'stop_outcome':('accepted','unknown','rejected','not_sent'),
                       'stop_cause':('manual','backend_restart','internal_error','local_mode_failed','vehicle_changed',
                                     'local_data_missing','charger_mode_changed','phase_timeout','start_access_lost',
                                     'overshoot_guard','time_guard','external_power_stop','no_charging_power','start_rejected','phase_resume_failed')}),
            'car': {**selected(car, numbers=('soc',), flags=('available','stale'), dates=('captured_at','read_at'),
                              enums={'code':('ok','partial','network','quota_reserved','rate_limit','auth','forbidden','not_found','unsupported','protocol','upstream','not_configured'),
                                     'state':('CHARGING','CONNECT_CABLE','CONSERVING','READY_FOR_CHARGING','DISCHARGING','CHARGING_INTERRUPTED'),
                                     'plug':('CONNECTED','DISCONNECTED'),'plug_lock':('LOCKED','UNLOCKED')}),
                    'quota_remaining': quota},
            'vehicle_charging': {**selected(settings,
                 numbers=('target_percent','battery_care_target_percent','max_current_a'),
                 flags=('advertises_start_charging','advertises_stop_charging','advertises_set_charge_mode','advertises_set_charging_limit'),
                 enums={'preferred_mode':CHARGE_MODES,'battery_care_mode':('ACTIVATED','DEACTIVATED')}),
                 'available_modes':modes},
            'charger': selected(charger, numbers=('power_kw','status_code','info_code','offered_current_a','phase_count','phase_switch_seconds'), flags=('available','telemetry_available','ready_for_start','phase_switching'),
                                dates=('read_at',), enums={'mode': ('OFF','A+','M+','A','M')}),
            'session': selected(session, numbers=('target_percent','last_start_epoch','fresh_soc_deadline',
                        'start_attempts', 'power_cycle_attempts', 'next_start_epoch', 'start_eta_seconds','completed_vehicle_soc',
                        'data_missing_since','stop_intent_epoch','stop_requested_epoch','stop_failures','last_stop_failure_epoch','phase_start_deadline_epoch','phase_ready_since_epoch','stop_trigger_soc','target_probe_count','freshness_probe_count',
                        'stop_trigger_quota_remaining','stop_trigger_cooldown_seconds','stop_trigger_quota_reset_seconds'),
                        flags=('awaiting_fresh_soc','stop_requested','stop_guard','auto_restored','vehicle_stop_confirmed','phase_start_pending'),
                        dates=('started_at','completed_at','completed_vehicle_capture','stop_requested_at','stop_trigger_at','stop_trigger_captured_at','stop_trigger_read_at','target_probe_capture','target_probe_scheduled_at','freshness_probe_capture','freshness_probe_scheduled_at','start_confirmed_at'),
                        enums={'status': ACTIVE | {'idle','complete','closed','error'},
                               'strategy':('smartfox_energy',),
                               'manual_phase':('preparing','waiting_power','phase_switching','charging'),
                               'stop_cause':('target','estimated_target','uncertainty_limit','energy_data_missing','charger_unavailable','start_timeout','start_rejected','missing_initial_soc','missing_soc','phase_wait','request_quota','vehicle_limit_start','vehicle_limit_lost'),
                               'start_confirmation_source':('smartfox_power',)}),
            'energy_estimate': selected(session.get('energy_estimate') or session.get('energy_meter'),
                numbers=('estimated_soc','total_kwh','anchor_soc','anchor_epoch','vehicle_capture',
                         'initial_report_age_seconds','capacity_kwh','efficiency','gap_seconds','upper_soc','uncertain_kwh'), flags=('valid',)),
            'vehicle_limit': selected(session.get('vehicle_limit'), numbers=('target_percent','restore_percent','put_epoch','deadline_epoch','probe_count',
                         'target_probe_count','target_deadline_epoch','released_at',
                         'target_write_at','target_write_http_status','target_write_quota_remaining','target_write_cooldown_seconds','target_write_quota_reset_seconds',
                         'restore_write_at','restore_write_http_status','restore_write_quota_remaining','restore_write_cooldown_seconds','restore_write_quota_reset_seconds'),
                       flags=('cancelled','retarget'), dates=('baseline_capture','verified_at'),
                       enums={'phase':('setting','verified','restore_needed','restoring','restored','attention','rejected','not_sent','released'),
                              'target_write_outcome':('accepted','unknown','rejected'), 'restore_write_outcome':('accepted','unknown','rejected'),
                              'target_write_code':('network','protocol','rate_limit','quota_reserved','auth','forbidden','not_found','unsupported','upstream'),
                              'restore_write_code':('network','protocol','rate_limit','quota_reserved','auth','forbidden','not_found','unsupported','upstream'),
                              'target_write_problem_type':('api-key-expired','api-key-not-authorized','operation-not-authorized','operation-not-supported','operation-disabled','rate-limit-exceeded','vehicle-not-accepting-requests'),
                              'restore_write_problem_type':('api-key-expired','api-key-not-authorized','operation-not-authorized','operation-not-supported','operation-disabled','rate-limit-exceeded','vehicle-not-accepting-requests'),
                              'target_verification':('unconfirmed',),
                              'outcome':('not_sent','accepted','unknown','rejected'), 'restore_source':('reported','user_default'),
                              'restoration_confirmation':('api','user')}),
            'last_command': selected(command, dates=('requested_at',), enums={'action':('start','stop'),
                                    'status':('accepted','unknown','rejected','observed','unconfirmed')}),
            'monitoring': {**selected(status.get('monitoring', {}), flags=('running',)),
                           **selected(status,numbers=('next_skoda_seconds','freshness_limit_seconds'),dates=('server_at',))}}
