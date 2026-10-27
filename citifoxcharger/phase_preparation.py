"""Prepare charger phases before the vehicle Start, then release M+.

Persisted steps: prepare -> settling -> vehicle_start -> release_mplus -> done.
The original A/A+ policy is restored once M+ is active. An original M/M+
policy is restored on exit. No vehicle profile or current limit is written.
Legacy sessions without this record retain their previous charging strategy.
"""
import time
import math

from .skoda import IntegrationError, timestamp
from .smartfox_web import SmartfoxControlError

STABLE_SECONDS = 20
PREPARATION_SECONDS = 600


class PhasePreparation:
    def __init__(self, manual):
        self.manual = manual
        self.session, self.service = manual.session, manual.service
        self.next_restore = 0

    @property
    def record(self):
        value = self.session.value.get('phase_preparation')
        if not value:
            return {}
        if (not isinstance(value, dict)
                or value.get('phase') not in ('prepare','settling','vehicle_start','start_recorded','release_mplus','done','cancelled')
                or any(type(value.get(key)) is not int or value[key] not in (0,1,2) for key in ('original_auto','original_manual'))
                or any(type(value.get(key)) is not bool for key in ('restore_auto','restore_manual'))
                or value.get('base_mode') not in ('A+','M+')
                or type(value.get('deadline_epoch')) not in (int,float) or not math.isfinite(value['deadline_epoch'])):
            return {'phase':'invalid'}
        return value

    @property
    def pending_restore(self):
        return bool(self.record.get('phase') == 'invalid' or self.record.get('restore_auto') or self.record.get('restore_manual'))

    def save(self, **changes):
        self.session.save(phase_preparation={**self.record, **changes})

    def begin(self, charger):
        # Optional adapter capability; saved legacy/injected adapters remain compatible.
        if not callable(getattr(self.session.web, 'read_phase_settings', None)):
            return None
        settings = self.session.web.read_phase_settings(self.service.config.value['smartfox'])
        return {'phase': 'prepare', 'original_auto': settings['auto_mode'],
                'original_manual': settings['manual_mode'], 'restore_auto': False,
                'restore_manual': False, 'base_mode': 'M+' if charger.get('mode') == 'M+' else 'A+',
                'deadline_epoch': time.time()+PREPARATION_SECONDS}

    def fail(self, message):
        attempted = self.record.get('phase') in ('start_recorded', 'release_mplus')
        observing = attempted or self.record.get('base_mode') == 'M+'
        self.save(phase='cancelled')
        self.session.save(status='paused' if observing else 'error', message=message)
        self.cleanup(final=True)

    def cleanup(self, final=False):
        """Restore only our own override; do not replace a different external choice."""
        record = self.record
        if record.get('phase') == 'invalid':
            return  # Never derive a device write from an invalid saved record.
        if not self.pending_restore or (not final and record.get('phase') != 'done'):
            return
        if time.monotonic() < self.next_restore:
            return
        self.next_restore = time.monotonic()+15
        config = self.service.config.value['smartfox']
        try:
            charger = self.session.web.read(config)
            current = self.session.web.read_phase_settings(config)
            requested, cleared = {}, {}
            for group, key in (('auto', 'auto_mode'), ('manual', 'manual_mode')):
                if not record.get('restore_'+group):
                    continue
                original = record['original_'+group]
                if current[key] == original or current[key] != 2:
                    cleared['restore_'+group] = False
                elif ((group == 'auto' and (final or charger.get('mode') == 'M+'))
                      or (final and charger.get('mode') in ('A+', 'A', 'OFF'))):
                    requested[key] = original
            if requested:
                self.session.web.set_phase_settings(config, **requested)
                cleared.update({'restore_'+key.split('_')[0]: False for key in requested})
            if cleared:
                self.save(**cleared)
            if self.record.get('restore_error'):
                self.save(restore_error=False)
        except SmartfoxControlError:
            self.save(restore_error=True)
            if not self.session.active:
                self.session.save(message='Restoring the original SMARTFOX phase settings is pending. Check the connection.')

    def tick(self, charger):
        """True means preparation owns the start; normal energy monitoring still runs."""
        record = self.record
        if record.get('phase') == 'invalid':
            self.session.save(status='error', message='Saved phase preparation is invalid. Check the SMARTFOX phase settings before starting again.')
            return True
        if not record or record.get('phase') == 'done':
            return False
        if record.get('phase') == 'cancelled':
            return True
        config = self.service.config.value['smartfox']
        now = time.time()
        if record.get('interrupted'):
            # Never replay an API request or a pending M+ release after restart.
            if charger.get('mode') == 'M+' and record.get('command_status') == 'accepted':
                self.save(phase='done', observe_only=True)
                self.cleanup()
                return False
            self.fail('Start preparation was interrupted. Original phase settings are being restored; no new Start was sent.')
            return True
        if record['phase'] in ('prepare', 'settling') and now > record['deadline_epoch']:
            self.fail('Three-phase preparation was not confirmed within ten minutes. No M+ release was sent.')
            return True
        if now > self.session.value['preparation_deadline']:
            self.fail('Vehicle start access did not become available in time. No M+ release was sent.')
            return True
        try:
            if record['phase'] == 'prepare':
                # Persist restoration intent before any device setting changes.
                self.save(restore_auto=record['base_mode'] == 'A+' and record['original_auto'] != 2,
                          restore_manual=record['original_manual'] != 2)
                self.session.web.set_phase_settings(config,
                    auto_mode=2 if record['base_mode'] == 'A+' else None, manual_mode=2)
                charger = self.session.web.set_mode(config, record['base_mode'])
                self.service.cache['smartfox']['charger'] = charger
                self.save(phase='settling', applied_epoch=now, stable_capture=None)
                self.session.save(status='starting', manual_phase='preparing',
                    message='Preparing three phases. Start time unknown; waiting for SMARTFOX.')
                return True
            if record['phase'] == 'settling':
                if charger.get('mode') != record['base_mode']:
                    self.fail('Charger mode changed during phase preparation. No vehicle Start was sent.')
                    return True
                capture = timestamp(charger.get('read_at'))
                deferred = (record['base_mode'] == 'A+' and charger.get('phase_count') == 1
                            and not charger.get('phase_switching') and charger.get('power_kw') == 0)
                ready = (capture is not None and record['applied_epoch'] <= capture <= now+5
                         and -5 <= now-capture <= 30 and (charger.get('phase_count') == 3 or deferred)
                         and not charger.get('phase_switching'))
                if not ready:
                    self.save(stable_capture=None)
                    seconds = charger.get('phase_switch_seconds')
                    message = (f'Phase switching: about {seconds} s remaining, then checking charging permission.'
                               if charger.get('phase_switching') and type(seconds) is int else
                               'Preparing three phases. Start time unknown; waiting for SMARTFOX.')
                    self.session.save(status='starting', manual_phase='preparing', message=message)
                    return True
                stable = record.get('stable_capture')
                if stable is None:
                    self.save(stable_capture=capture)
                    stable = capture
                remaining = max(0, int(STABLE_SECONDS-(capture-stable)))
                if deferred:
                    # Verified on hardware: idle A+ can retain a 1ph indication
                    # despite a confirmed 3ph policy. Do not deadlock Start.
                    self.save(deferred_switch=True)
                    remaining = 0
                if remaining:
                    self.session.save(message=f'Three phases reported. Checking stable permission: about {remaining} s.')
                    return True
                self.save(phase='vehicle_start')
            if self.record['phase'] == 'vehicle_start':
                if not self.service.manual_access_configured():
                    self.fail('Vehicle start access is unavailable. Original phase settings are being restored.')
                    return True
                if not self.service.can_manual_command():
                    adapter = self.service.car
                    wait = max(0, self.service.command_after-time.monotonic(), adapter.blocked_until-adapter.clock())
                    if adapter.quota_remaining == '0':
                        wait = max(wait, adapter.quota_reset_at-adapter.clock())
                    self.session.save(next_start_epoch=now+wait,
                        message=(f'Three-phase policy selected. Waiting for vehicle command access: about {math.ceil(wait)} s.' if wait > 0 else
                                 'Three-phase policy selected. Waiting for vehicle command access; start time unknown.'))
                    return True
                count = self.session.value.get('start_attempts', 0)+1
                # A crash or timeout must not duplicate this request.
                self.save(phase='start_recorded')
                self.session.save(start_attempts=count, power_cycle_attempts=1,
                                  last_start_epoch=now, next_start_epoch=now+180)
                result = self.service.command('start', f"{self.session.value['request_id']}-attempt-{count}", manual=True)
                if result.get('status') != 'accepted':
                    self.fail('Vehicle Start was not confirmed accepted. No M+ release was sent; check MySkoda.')
                    return True
                self.save(phase='release_mplus', command_status='accepted')
            if self.record['phase'] == 'release_mplus':
                charger = self.session.web.set_mode(config, 'M+')
                self.service.cache['smartfox']['charger'] = charger
                self.save(phase='done')
                self.session.save(status='starting', manual_phase='waiting_power', ready_delay_seconds=90,
                                  message='Vehicle Start accepted; M+ confirmed. Waiting for charging power.')
                self.cleanup()
                return False
        except (SmartfoxControlError, IntegrationError):
            if self.record.get('command_status') == 'accepted':
                # The M+ write may already have reached the device. Keep the
                # energy/target monitor rather than abandoning an unknown load.
                self.save(phase='done', observe_only=True)
                self.session.save(status='paused', message='M+ release is unconfirmed. Target monitoring remains active; check SMARTFOX.')
            else:
                self.fail('Start preparation could not be confirmed. Original phase settings are being restored; check SMARTFOX and MySkoda.')
        return True
