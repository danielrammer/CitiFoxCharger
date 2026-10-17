import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from citifoxcharger.lifecycle import LocalControl, ControlError, BackendUnavailable
from citifoxcharger.smartfox_energy import energy_display, quantity


class EnergyTests(unittest.TestCase):
    def setUp(self):
        self.fields={"hidPower":"600 W","hidProduction":"0.00 kW","batteryPower":"0.00 kW",
                     "hidAoutActivated":"1","analogOutPower":"0.00 <span>kW</span>","analogOutPercent":"0%",
                     "analogOutTemp":"45.5 °C, 52.2 °C","hidAoutTemperatureSensors":"1","hidAoutStatus":"0",
                     "hidRelay1Activated":"1","hidRelay2Activated":"1","hidR1Status":"0","hidR2Status":"1",
                     "hidR1Info":"0","hidR2Info":"2","hidR1TempSensor":"1","hidR2TempSensor":"0"}

    def display(self): return energy_display(self.fields,"2026-10-16T19:00:00Z")

    def test_house_grid_import_and_actual_relay_states(self):
        value=self.display()
        self.assertEqual(value["house_kw"],.6)
        self.assertEqual(value["grid_kw"],.6)
        self.assertEqual(value["heater"]["power_kw"],0)
        self.assertEqual(value["heater"]["temperature_c"],45.5)
        self.assertFalse(value["heater2"]["on"])
        self.assertTrue(value["hot_water"]["on"])
        self.assertIsNone(value["hot_water"]["temperature_c"])
        self.assertNotIn("power_kw",value["heater2"])

    def test_grid_export_battery_sign_and_nonnegative_house(self):
        self.fields.update(hidPower="-2500 W",hidProduction="6.00 kW",batteryPower="1.00 kW")
        self.assertEqual(self.display()["house_kw"],2.5)
        self.assertEqual(self.display()["grid_kw"],-2.5)
        self.fields["hidProduction"]="0 kW"
        self.assertEqual(self.display()["house_kw"],0)

    def test_missing_or_invalid_values_are_unknown_not_zero(self):
        for value in ("", "600 kWh", "nan W", "<b>600 W</b>", "999999999999 W"):
            self.fields["hidPower"]=value
            with self.subTest(value=value):
                self.assertIsNone(self.display()["house_kw"])
                self.assertIsNone(self.display()["grid_kw"])
        self.fields.pop("hidProduction")
        self.fields["hidPower"]="600 W"
        self.assertIsNone(self.display()["house_kw"])
        self.assertEqual(self.display()["grid_kw"],.6)

    def test_temperature_sensor_selection_and_missing_sentinel(self):
        self.fields["hidAoutTemperatureSensors"]="2"
        self.assertEqual(self.display()["heater"]["temperature_c"],52.2)
        self.fields["analogOutTemp"]="850.0 °C, -12,5 °C"
        self.assertIsNone(self.display()["heater2"]["temperature_c"])
        self.assertEqual(self.display()["heater"]["temperature_c"],-12.5)
        self.fields["analogOutTemp"]="error °C, 52.2 °C"
        self.assertIsNone(self.display()["heater2"]["temperature_c"])
        self.assertEqual(self.display()["heater"]["temperature_c"],52.2)
        self.fields["hidAoutTemperatureSensors"]="3"
        self.assertIsNone(self.display()["heater"]["temperature_c"])

    def test_inactive_and_unknown_relays_never_appear_as_measured_zero(self):
        self.fields.update(hidAoutActivated="0",hidRelay1Activated="0",hidR2Status="x",hidR2Info="255")
        value=self.display()
        self.assertIsNone(value["heater"]["power_kw"])
        self.assertIsNone(value["heater2"]["on"])
        self.assertIsNone(value["hot_water"]["on"])
        self.assertEqual(value["hot_water"]["status"],"Status unbekannt")

    def test_units_decimal_comma_and_invalid_percent(self):
        self.assertEqual(quantity(" 1,25 <span>kW</span>","kW",0,10),1.25)
        self.fields["analogOutPercent"]="101%"
        self.assertIsNone(self.display()["heater"]["percent"])


class LifecycleTests(unittest.TestCase):
    def recovery_status(self,**changes):
        from datetime import datetime,timezone
        return {'charging':{'stop_requested':True},'smartfox':{'charger':{
            'available':True,'mode':'OFF','read_at':datetime.now(timezone.utc).isoformat(),**changes}}}

    def test_recovery_stop_checks_persisted_intent_even_when_an_old_backend_returns_a_cloud_error(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value={'app':'CitiFoxCharger'})
        control.request=MagicMock(side_effect=[{'csrf':'test-csrf'},{'status':'confirmed'},ControlError('Cloud unavailable'),self.recovery_status()])
        self.assertIn('vehicle confirmation may still be pending',control.stop_charging())
        calls=control.request.call_args_list
        self.assertEqual([c.args[0] for c in calls],['/api/session','/api/smartfox/charger-mode','/api/charging/stop','/api/status'])
        self.assertEqual(calls[1].args[1]['mode'],'OFF')
        self.assertEqual(calls[1].args[2],'test-csrf')
        self.assertNotEqual(calls[1].args[1]['request_id'],calls[2].args[1]['request_id'])

    def test_recovery_never_starts_a_backend_when_none_is_running(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value=None)
        control.request=MagicMock()
        with patch('citifoxcharger.lifecycle.spawn_backend') as spawn:
            with self.assertRaisesRegex(ControlError,'not running'):control.action('stop-charging',Path('unused'),8765)
            spawn.assert_not_called()
        control.request.assert_not_called()

    def test_recovery_rejects_missing_stop_intent(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value={'app':'CitiFoxCharger'})
        status=self.recovery_status();status['charging']['stop_requested']=False
        control.request=MagicMock(side_effect=[{'csrf':'test'},{},{},status])
        with self.assertRaisesRegex(ControlError,'stop request is unconfirmed'):control.stop_charging()

    def test_recovery_rejects_invalid_status_without_claiming_a_stop(self):
        for status in (None,[],{'charging':[]},{'smartfox':None}):
            with self.subTest(status=status):
                control=LocalControl(8765)
                control.health=MagicMock(return_value={'app':'CitiFoxCharger'})
                control.request=MagicMock(side_effect=[{'csrf':'test'},{},{},status])
                with self.assertRaisesRegex(ControlError,'Stop status is unavailable'):control.stop_charging()

    def test_recovery_still_requests_vehicle_stop_after_local_failure_and_never_claims_off(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value={'app':'CitiFoxCharger'})
        control.request=MagicMock(side_effect=[{'csrf':'test'},ControlError('Local offline'),{},self.recovery_status(mode='M+')])
        with self.assertRaisesRegex(ControlError,'Off is unconfirmed'):control.stop_charging()
        self.assertEqual(control.request.call_args_list[2].args[0],'/api/charging/stop')

    def test_recovery_rejects_old_or_unavailable_local_readback(self):
        for change in ({'read_at':'2000-01-01T00:00:00+00:00'},{'available':False},{'read_at':None}):
            with self.subTest(change=change):
                control=LocalControl(8765)
                control.health=MagicMock(return_value={'app':'CitiFoxCharger'})
                control.request=MagicMock(side_effect=[{'csrf':'test'},{},{},self.recovery_status(**change)])
                with self.assertRaisesRegex(ControlError,'Off is unconfirmed'):control.stop_charging()

    def test_stop_uses_authenticated_quit_and_waits_for_shutdown(self):
        control=LocalControl(8765)
        control.health=MagicMock(side_effect=[{"app":"CitiFoxCharger","instance_id":"a"},None])
        control.request=MagicMock(side_effect=[{"csrf":"test-csrf"},{"message":"ok"}])
        self.assertIn("stopped",control.action("stop",Path("unused"),8765))
        self.assertEqual(control.request.call_args_list[-1].args,("/api/app/quit",{},"test-csrf"))

    def test_restart_waits_for_new_instance_and_never_sends_charging_commands(self):
        control=LocalControl(8765)
        control.health=MagicMock(side_effect=[{"instance_id":"old"},None,{"instance_id":"new"}])
        control.request=MagicMock(side_effect=[{"csrf":"test-csrf"},{}])
        with patch("citifoxcharger.lifecycle.time.sleep"):
            message=control.action("restart",Path("unused"),8765,no_browser=True)
        self.assertIn("no new charging start",message)
        self.assertEqual([c.args[0] for c in control.request.call_args_list],["/api/session","/api/app/restart"])

    def test_shutdown_waits_through_temporarily_bound_unresponsive_port(self):
        control=LocalControl(8765)
        control.health=MagicMock(side_effect=[{"instance_id":"old"},BackendUnavailable("closing"),None])
        control.request=MagicMock(side_effect=[{"csrf":"test"},{}])
        with patch("citifoxcharger.lifecycle.time.sleep"):
            self.assertIn("stopped",control.action("stop",Path("unused"),8765))

    def test_rejected_stop_is_not_forced(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value={"instance_id":"a"})
        control.request=MagicMock(side_effect=[{"csrf":"test"},ControlError("Monitoring active")])
        with self.assertRaisesRegex(ControlError,"Monitoring"):
            control.action("stop",Path("unused"),8765)

    def test_stopped_backend_is_a_successful_noop(self):
        control=LocalControl(8765)
        control.health=MagicMock(return_value=None)
        control.request=MagicMock()
        self.assertIn("already stopped",control.action("stop",Path("unused"),8765))
        control.request.assert_not_called()

    @patch("citifoxcharger.lifecycle.urllib.request.build_opener")
    def test_foreign_process_is_never_stopped(self,opener):
        response=opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value=b'{"app":"Another app"}'
        with self.assertRaises(ControlError): LocalControl(8765).health()
