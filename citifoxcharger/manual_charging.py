"""Normal manual charge: prepare -> wait for power -> charge -> existing stop.

The persisted strategy separates new energy-backed sessions from legacy saved
sessions and the isolated vehicle-limit experiment. The ChargingSession owns
storage and local-first stopping; this controller owns preparation and running.
It never writes a vehicle limit or a charging profile.
"""
import copy
import time
from . import charge_energy
from .modbus import utcnow
from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError
from .phase_preparation import PhasePreparation
from .charging_window import AUTOMATIC_RETRY_SECONDS

STRATEGY = 'smartfox_energy'
RECONNECT_SETTLE_SECONDS = 10


class ManualCharging:
    def __init__(self, session):
        self.session = session
        self.service = session.service
        self.phases = PhasePreparation(self)

    @property
    def selected(self):
        return self.session.value.get('strategy') == STRATEGY

    def local_ready(self, charger=None):
        c = charger if charger is not None else self.service.cache.get('smartfox', {}).get('charger', {})
        captured = timestamp(c.get('read_at'))
        return bool(c.get('available') and c.get('control_available') and c.get('telemetry_available')
                    and captured is not None and -5 <= time.time()-captured <= 30
                    and c.get('status_code') in (2, 3, 5, 6) and c.get('info_code') != 14
                    and charge_energy.finite(c.get('power_kw')) and 0 <= c['power_kw'] <= 50)

    def reason(self):
        s, value = self.service, self.session.value
        car = s.cache.get('skoda', {})
        if s.stopping or s.app_update_pending:
            return 'The app is restarting or updating.'
        if not self.session.active and self.phases.pending_restore:
            return 'The original SMARTFOX phase settings are still being restored.'
        if self.session.active and (not self.selected or value.get('stop_requested')):
            return 'Finish the current session or pending stop first.'
        if not self.local_ready():
            return 'SMARTFOX must confirm a connected, ready or charging vehicle.'
        if not s.manual_access_configured():
            return 'Vehicle start access is missing or expired. Check Settings.'
        captured = timestamp(car.get('captured_at'))
        if type(car.get('soc')) is not int or captured is None or not -5 <= time.time()-captured <= 86400:
            return 'A vehicle charge level from the last 24 hours is required.'
        return None

    def state(self, result):
        reason = self.reason()
        result.update(can_start_manual=reason is None, can_start_manual_with_stale=reason is None,
                      manual_start_reason=reason, target_control='energy' if self.selected else 'app')
        if self.selected:
            # History stays on disk; browser clients get only the useful summary.
            result.pop('energy_meter', None)
            meter = self.session.value.get('energy_meter', {})
            result['energy_estimate'] = {key: meter.get(key) for key in
                ('estimated_soc', 'total_kwh', 'valid', 'anchor_soc', 'anchor_epoch',
                 'vehicle_capture', 'initial_report_age_seconds', 'capacity_kwh', 'efficiency', 'gap_seconds', 'upper_soc', 'uncertain_kwh')}
            result['can_retry_start'] = False
            result['can_retry_start_with_stale'] = False
            result['can_adjust_target'] = self.session.active and not result.get('stop_requested')
            wait = max(0, int(self.session.value.get('next_start_epoch', time.time())-time.time()))
            ready = self.session.value.get('ready_since_epoch')
            if ready:
                wait = max(wait, int(ready+self.session.value.get('ready_delay_seconds', 3)-time.time()), 0)
            c = self.service.cache.get('smartfox', {}).get('charger', {})
            if c.get('phase_switching'):
                seconds = c.get('phase_switch_seconds')
                wait = max(wait, seconds if type(seconds) is int else 120)
            result['start_eta_seconds'] = wait if self.session.value.get('manual_phase') != 'charging' else 0
            if self.phases.pending_restore:
                result['can_shutdown'] = False
        return result

    def start(self, target, request_id):
        session, s = self.session, self.service
        if type(target) is not int or not 1 <= target <= 100:
            raise ValueError('Target charge level must be between 1 and 100%.')
        if request_id == session.value.get('request_id'):
            if target != session.value.get('target_percent'):
                raise ValueError('This request ID belongs to a different target.')
            return session.state()
        # Read local readiness before committing intent; A+ charging counts too.
        if session.web is None:
            raise IntegrationError('blocked', 'Local SMARTFOX control is unavailable.')
        s.cache.setdefault('smartfox', {})['charger'] = session.web.read(s.config.value['smartfox'])
        reason = self.reason()
        if reason:
            raise IntegrationError('blocked', reason)
        if session.active:
            # Clicking again updates the target; it never creates a second monitor.
            session.save(target_percent=target)
            self.tick()
            return session.state()
        car = s.cache['skoda']
        if car['soc'] >= target:
            raise IntegrationError('target_reached', 'The selected target is already reached.')
        now = time.time()
        c = s.cache['smartfox']['charger']
        try:
            phase_preparation = self.phases.begin(c)
        except SmartfoxControlError:
            # Preselection is an optimization. A missing settings form must
            # not remove the existing, verified mode/Start charging path.
            phase_preparation = None
            s.event('Phase preselection unavailable. Using the existing SMARTFOX manual-start path.')
        meter = charge_energy.begin(car['soc'], timestamp(car['captured_at']), now, c['power_kw'])
        session.save(replace=True, strategy=STRATEGY, status='starting', manual_phase='preparing',
                     automatic_window=s.charging_policy.window() if s.charging_policy.busy else None,
                     vin=s.config.value['skoda']['vin'], target_percent=target, request_id=request_id,
                     started_at=utcnow(), last_start_epoch=now, energy_meter=meter,
                     start_attempts=0, power_cycle_attempts=0, next_start_epoch=now, preparation_deadline=now+3900,
                     phase_preparation=phase_preparation,
                     message='Start attempted. Preparing M+; waiting for charging power.')
        self.tick()
        return session.state()

    def stop(self, cause, message):
        if self.automatic_window_open() and cause not in ('target', 'estimated_target'):
            if (not self.session.value.get('stop_requested')
                    and self.session.value.get('manual_phase') not in ('disconnected', 'automatic_wait')):
                self.service.charging_policy.record_abort()
            self.session.save(automatic_resume_after=time.time()+AUTOMATIC_RETRY_SECONDS)
        self.session.automatic_stop(message, cause=cause)

    def tick(self):
        try:
            self._tick()
        except OSError:
            self.secure_storage_failure()
            raise

    def secure_storage_failure(self, message='Local storage failed. Securing charger Off; target unconfirmed.'):
        # Storage failure must not leave an unrecorded manual charge running.
        self.session.value.update(status='paused', stop_requested=True, stop_guard=True,
                                  auto_restored=False, stop_cause='energy_data_missing',
                                  message=message)
        try:
            c = self.session.web.set_charger_mode(self.service.config.value['smartfox'], 'OFF')
            self.service.cache.setdefault('smartfox', {})['charger'] = c
            self.session.value['auto_restored'] = True
        except SmartfoxControlError:
            pass

    def check_target(self):
        try:
            return self.session.stop_if_target_reached()
        except OSError:
            self.secure_storage_failure()
            raise

    def automatic_window_open(self):
        policy = self.service.charging_policy
        window = self.session.value.get('automatic_window')
        return bool(window and policy.value['only'] and policy.value['automatic'] and policy.window() == window
                    and not policy.is_blocked(window) and self.session.value.get('manual_stop_window') != window)

    def retry_automatic(self):
        """Rearm a stopped automatic monitor only after its existing stop guard clears."""
        session, s, now = self.session, self.service, time.time()
        value = session.value
        deadline = value.get('automatic_resume_after')
        if (not self.selected or session.active or not self.automatic_window_open()
                or not charge_energy.finite(deadline) or value.get('ended_by_user')
                or value.get('stop_cause') in ('target', 'estimated_target')
                or value.get('vin') != s.config.value['skoda']['vin']
                or value.get('stop_guard') or not value.get('vehicle_stop_confirmed')
                or not value.get('auto_restored') or self.reason()):
            return False
        charger = s.cache.get('smartfox', {}).get('charger', {})
        if charger.get('mode') != 'OFF' or charger.get('power_kw', 50) > .1 or charger.get('phase_switching'):
            return False
        meter = copy.deepcopy(value.get('energy_meter', {}))
        car = s.cache.get('skoda', {})
        if not meter.get('valid'):
            return False
        charge_energy.observe(meter, timestamp(charger.get('read_at')), charger['power_kw'],
                              car.get('soc'), timestamp(car.get('captured_at')))
        session.save(energy_meter=meter)
        target = value['target_percent']
        if (type(car.get('soc')) is int and car['soc'] >= target
                or meter['estimated_soc'] >= target
                or meter['upper_soc'] >= (min(target+5, 99) if target < 100 else 100)):
            return False
        session.save(status='interrupted', manual_phase='automatic_wait',
                     automatic_resume_after=max(deadline, now+AUTOMATIC_RETRY_SECONDS),
                     next_start_epoch=max(deadline, now+AUTOMATIC_RETRY_SECONDS),
                     automatic_disconnected_at=charger['read_at'],
                     automatic_counted_start_epoch=value.get('last_start_epoch'),
                     automatic_generation=value.get('automatic_generation', 0)+1,
                     stop_requested=False, stop_guard=False, vehicle_stop_confirmed=False,
                     auto_restored=False, stop_cause=None, stop_reason=None,
                     stop_requested_epoch=None, stop_intent_epoch=None, stop_id=None,
                     reconnect_ready_since=None,
                     message='Automatic charging paused. Waiting three minutes before the next attempt.')
        return True

    def pause_automatic(self, charger):
        """Keep the same automatic session through unplugging or a zero-power pause."""
        session, s, now = self.session, self.service, time.time()
        capture = timestamp(charger.get('read_at'))
        fresh = (charger.get('available') and charger.get('telemetry_available') is True
                 and capture is not None and -5 <= now-capture <= 30
                 and charge_energy.finite(charger.get('power_kw')) and 0 <= charger['power_kw'] <= 50)
        unplugged = (fresh and charger.get('status_code') in (0, 1, 255)
                     and charger.get('info_code') != 14 and charger['power_kw'] <= .1)
        attempt_failed = (session.value.get('manual_phase') == 'waiting_power'
                          and session.value.get('start_attempts', 0) > 0
                          and session.value.get('last_start_epoch') != session.value.get('automatic_counted_start_epoch')
                          and now-session.value.get('last_start_epoch', now) >= AUTOMATIC_RETRY_SECONDS)
        interrupted = (fresh and charger['power_kw'] <= .1 and not charger.get('phase_switching')
                       and self.local_ready(charger)
                       and (session.value.get('last_power_epoch') is not None
                            or attempt_failed
                            or (charger.get('mode') == 'OFF' and session.value.get('manual_phase') != 'preparing')))
        waiting = session.value.get('manual_phase') in ('disconnected', 'automatic_wait')
        blocked = False
        if not waiting:
            if not (unplugged or interrupted) or not self.automatic_window_open():
                return False
            blocked = s.charging_policy.record_abort()
            deadline = now if attempt_failed else now+AUTOMATIC_RETRY_SECONDS
            session.save(status='interrupted', manual_phase='disconnected' if unplugged else 'automatic_wait',
                         automatic_resume_after=deadline,
                         next_start_epoch=deadline,
                         automatic_counted_start_epoch=session.value.get('last_start_epoch'),
                         automatic_disconnected_at=charger['read_at'], reconnect_ready_since=None,
                         message='Automatic charging paused. Waiting three minutes before the next attempt.')
            if self.phases.record and self.phases.record.get('phase') != 'invalid':
                self.phases.save(phase='cancelled')

        # Keep local Off throughout the interruption, including after an external
        # schedule change. This does not stop or restart the vehicle through the API.
        try:
            if not fresh or charger.get('mode') != 'OFF':
                s.cache.setdefault('smartfox', {})['charger'] = session.web.set_charger_mode(s.config.value['smartfox'], 'OFF')
        except SmartfoxControlError:
            self.stop('charger_unavailable', 'Wallbox Off is unconfirmed. Waiting for safe automatic recovery.')
            return True
        self.phases.cleanup(final=True)
        if blocked:
            session.automatic_stop('Three automatic aborts within one hour. Automatic restart is suspended until the next time window.',
                                   cause='automatic_retry_limit')
            return True

        meter = copy.deepcopy(session.value['energy_meter'])
        car = s.cache.get('skoda', {})
        if fresh:
            charge_energy.observe(meter, capture, charger['power_kw'], car.get('soc'), timestamp(car.get('captured_at')))
        else:
            meter['upper_soc'] = charge_energy.upper(meter, now)
        session.save(energy_meter=meter)
        if not meter['valid']:
            self.stop('energy_data_missing', 'SMARTFOX energy measurements are missing. Waiting for safe automatic recovery.')
            return True
        if meter['upper_soc'] >= (min(session.value['target_percent']+5, 99) if session.value['target_percent'] < 100 else 100):
            self.stop('uncertainty_limit', 'Possible charge level reached target plus 5 points. Charging stopped; actual SOC unconfirmed.')
            return True
        if meter['estimated_soc'] >= session.value['target_percent']:
            self.stop('estimated_target', f"Estimated target {session.value['target_percent']} % reached using measured charging energy.")
            return True
        if fresh and (charger.get('status_code') == 4 or charger.get('info_code') == 14):
            self.stop('charger_unavailable', 'SMARTFOX reports an error or missing authorization. Automatic charging will retry when ready.')
            return True

        held = s.cache['smartfox']['charger']
        ready = (fresh and self.automatic_window_open() and self.local_ready(charger)
                 and charger.get('mode') == 'OFF'
                 and self.local_ready(held) and held.get('mode') == 'OFF' and held['power_kw'] <= .1
                 and charger['power_kw'] <= .1 and not charger.get('phase_switching')
                 and not self.phases.pending_restore and not self.reason() and s.can_manual_command()
                 and capture > (timestamp(session.value.get('automatic_disconnected_at')) or now))
        if not ready:
            session.save(reconnect_ready_since=None,
                         message='Automatic charging is waiting for vehicle readiness or access.')
            return True
        since = session.value.get('reconnect_ready_since')
        if not charge_energy.finite(since) or capture < since:
            since = capture
        session.save(reconnect_ready_since=since, message='Automatic charging paused. Waiting three minutes before the next attempt.')
        if capture-since < RECONNECT_SETTLE_SECONDS or now < session.value['automatic_resume_after']:
            return True

        try:
            preparation = self.phases.begin(s.cache['smartfox']['charger'])
        except SmartfoxControlError:
            # The same optional phase-preselection fallback as an ordinary start.
            preparation = None
        session.save(status='starting', manual_phase='preparing', phase_preparation=preparation,
                     last_power_epoch=None, resume_power_since=None, power_cycle_attempts=0,
                     ready_since_epoch=None, ready_delay_seconds=3, phase_since_epoch=None,
                     next_start_epoch=now, preparation_deadline=now+3900, reconnect_ready_since=None,
                     message='Resuming automatic charging with the existing target and energy monitoring.')
        return True

    def _tick(self):
        session, s, now = self.session, self.service, time.time()
        value = session.value
        if not self.selected or not session.active or value.get('stop_requested') or s.stopping:
            return
        if value.get('vin') != s.config.value['skoda']['vin']:
            session.save(status='error', message='Vehicle identity changed. Manual monitoring was not transferred.')
            return
        if self.check_target():
            return
        if value.get('automatic_window') and not self.automatic_window_open():
            # The policy owns the window-end stop; no Start/M+ may get ahead of it.
            return
        session.observe_start_power()
        c = s.cache.get('smartfox', {}).get('charger', {})
        if self.pause_automatic(c):
            return
        if not self.local_ready(c):
            # Missing measurements widen uncertainty instead of stopping early.
            # A real device fault/disconnection still cancels the attempt.
            if c.get('available') and (c.get('status_code') not in (2, 3, 5, 6) or c.get('info_code') == 14):
                self.stop('charger_unavailable', 'SMARTFOX reports disconnection, an error or missing authorization.')
            else:
                meter = copy.deepcopy(value['energy_meter'])
                meter['upper_soc'] = charge_energy.upper(meter, now)
                session.save(energy_meter=meter, status='paused',
                             message='SMARTFOX measurements delayed. Manual charging continues with increased uncertainty.')
                if meter['upper_soc'] >= (min(value['target_percent']+5, 99) if value['target_percent'] < 100 else 100):
                    self.stop('uncertainty_limit', 'Possible charge level reached target plus 5 points. Charging stopped; actual SOC unconfirmed.')
            return
        meter = copy.deepcopy(value['energy_meter'])
        car = s.cache.get('skoda', {})
        charge_energy.observe(meter, timestamp(c['read_at']), c['power_kw'], car.get('soc'), timestamp(car.get('captured_at')))
        session.save(energy_meter=meter)
        if not meter['valid']:
            self.stop('energy_data_missing', 'SMARTFOX measurements have a gap. Charging stopped; target unconfirmed.')
            return
        if meter['upper_soc'] >= (min(value['target_percent']+5, 99) if value['target_percent'] < 100 else 100):
            self.stop('uncertainty_limit', 'Possible charge level reached target plus 5 points. Charging stopped; actual SOC unconfirmed.')
            return
        if meter['estimated_soc'] >= value['target_percent']:
            self.stop('estimated_target', f"Estimated target {value['target_percent']} % reached using measured charging energy.")
            return
        if self.phases.tick(c):
            return
        # Preparation may have released M+ and refreshed the local report.
        c = s.cache['smartfox']['charger']
        value = session.value
        # Hold M+ through schedules and phase switching. An explicit mode choice
        # in CitiFox ends this controller before changing the wallbox mode.
        if c.get('mode') == 'OFF' and value.get('manual_phase') != 'preparing':
            self.stop('charger_unavailable', 'The charger was switched Off. Manual charging ended.')
            return
        if c.get('mode') != 'M+':
            try:
                c = session.web.set_mode(s.config.value['smartfox'], 'M+')
                s.cache['smartfox']['charger'] = c
            except SmartfoxControlError:
                self.stop('charger_unavailable', 'M+ could not be confirmed. Manual charging ended.')
                return
        if c.get('phase_switching'):
            phase_since = value.get('phase_since_epoch') or now
            session.save(status='starting', manual_phase='phase_switching', phase_since_epoch=phase_since,
                         ready_since_epoch=None, ready_delay_seconds=15, message=session.phase_message(c))
            if now-phase_since > 600:
                self.stop('phase_wait', 'Phase switching did not finish within ten minutes.')
            return
        if c['power_kw'] > .1:
            if self.phases.record.get('observe_only'):
                self.phases.save(observe_only=False)
            capture = timestamp(c['read_at'])
            since = value.get('resume_power_since') if c['power_kw'] >= .5 else None
            since = since if since is not None else capture
            proven = c['power_kw'] >= .5 and capture-since >= 3
            session.save(status='monitoring', manual_phase='charging', last_power_epoch=now,
                         phase_since_epoch=None, ready_since_epoch=None, ready_delay_seconds=90,
                         resume_power_since=since if c['power_kw'] >= .5 else None,
                         power_cycle_attempts=0 if proven else value.get('power_cycle_attempts', 0),
                         message=f"Manual charging to {value['target_percent']} %; measured energy is monitored.")
            return
        # Only local, settled zero-power readiness permits a new command. Never
        # retry during switching, on a timer alone, or after measured power starts.
        ready_since = value.get('ready_since_epoch') or now
        settle = value.get('ready_delay_seconds', 3)
        session.save(status='starting', manual_phase='waiting_power', ready_since_epoch=ready_since,
                     phase_since_epoch=None, resume_power_since=None)
        command_wait = s.command_after-time.monotonic()
        api_wait = s.car.blocked_until-s.car.clock()
        next_start = max(value.get('next_start_epoch', now),
                         now+command_wait if command_wait > 0 else 0,
                         now+api_wait if api_wait > 0 else 0)
        if s.car.quota_remaining == '0' and s.car.quota_reset_at > s.car.clock():
            next_start = max(next_start, now+s.car.quota_reset_at-s.car.clock())
        wait = max(0, int(max(next_start, ready_since+settle)-now))
        session.save(next_start_epoch=next_start, message=f'Start attempted. Waiting for charging power; next check in about {wait} seconds.')
        last_power = value.get('last_power_epoch')
        if ((not last_power and now > value['preparation_deadline'])
                or (last_power and now-last_power > 600)):
            self.stop('start_timeout', 'No charging power confirmed. Manual charging needs attention.')
            return
        if now < next_start or now-ready_since < settle or not c.get('ready_for_start'):
            return
        if self.phases.record.get('observe_only'):
            session.save(status='paused', message='Observing the interrupted start after restart. No repeated vehicle Start; Stop remains available.')
            return
        attempts = value.get('power_cycle_attempts', 0 if last_power else value.get('start_attempts', 0))
        if attempts >= 3:
            if now >= next_start:
                self.stop('start_timeout', 'No charging power after three start attempts. Please check MyŠkoda.')
            return
        if not s.can_manual_command():
            return
        # Persist each attempt BEFORE sending; restart never duplicates an attempt.
        total = value.get('start_attempts', 0)+1
        session.save(start_attempts=total, power_cycle_attempts=attempts+1,
                     next_start_epoch=now+180, last_start_epoch=now)
        try:
            result = s.command('start', f"{value['request_id']}-attempt-{total}", manual=True)
            if result['status'] == 'rejected':
                self.stop('start_rejected', result['message'])
        except IntegrationError:
            # A cooldown/access change is rechecked on the next backend tick.
            session.save(message='Vehicle start is unavailable. Waiting for API access and local charging power.')
