import copy
import unittest
from urllib.parse import parse_qs,urlsplit
from ladezentrale.config import DEFAULT
from ladezentrale.smartfox_web import SmartfoxWeb,SmartfoxControlError
from test_charging import xml


class HeaterControlTests(unittest.TestCase):
    def setUp(self):
        self.config=copy.deepcopy(DEFAULT["smartfox"])
        self.mode,self.relay,self.relay2,self.percent=2,"0","0",42
        self.apply,self.paths=True,[]
        self.name,self.relay_type="Heizstab 2","0"
        self.firmware="EM3 00.01.16.08"
        self.web=SmartfoxWeb(self.request,allow_heater_control=True)

    def request(self,host,path,timeout):
        self.paths.append(path)
        if path.startswith("/setsw"):
            query=parse_qs(urlsplit(path).query)
            if self.apply:
                if path.startswith("/setswaout"):
                    self.mode=int(query["mode"][0])
                    if "power" in query:self.percent=int(query["power"][0])
                elif query.get("state") in (["0"],["1"],["2"]):
                    value={"0":"0","1":"m","2":"x"}[query["state"][0]]
                    if query["rel"]==["1"]:self.relay=value
                    else:self.relay2=value
            return b"OK"
        fields={"hidAoutActivated":"1","hidAoutName":"Heizstab","hidAoutMode":str(self.mode),
                "hidAoutPercentage":str(self.percent),"hidRelay1Activated":"1","hidR1Name":self.name,
                "hidR1Type":self.relay_type,"hidR1Mode":self.relay,
                "hidRelay2Activated":"1","hidR2Name":"Warmwasser","hidR2Type":"2","hidR2Mode":self.relay2}
        added="".join(f'<value id="{key}">{value}</value>' for key,value in fields.items()).encode()
        return xml(firmware=self.firmware).replace(b"</values>",added+b"</values>")

    def writes(self):return [path for path in self.paths if path.startswith("/setsw")]

    def test_analog_manual_uses_configured_percent_and_auto_is_code_two(self):
        result=self.web.set_heater_mode(self.config,"heater","manual")
        self.assertEqual(self.writes(),["/setswaout.cgi?mode=1&power=42"])
        self.assertEqual(result["energy"]["heater"]["mode"],"M")
        self.web.set_heater_mode(self.config,"heater","auto")
        self.assertEqual(self.writes()[-1],"/setswaout.cgi?mode=2")

    def test_relay_manual_cancels_timer_then_selects_on_and_auto_zero(self):
        result=self.web.set_heater_mode(self.config,"heater2","manual")
        self.assertEqual(self.writes(),["/setswrel.cgi?rel=1&state=3&duration=0","/setswrel.cgi?rel=1&state=1"])
        self.assertEqual(result["energy"]["heater2"]["mode"],"M")
        self.web.set_heater_mode(self.config,"heater2","auto")
        self.assertEqual(self.writes()[-1],"/setswrel.cgi?rel=1&state=0")

    def test_already_selected_mode_does_not_write(self):
        self.web.set_heater_mode(self.config,"heater","auto")
        self.assertEqual(self.writes(),[])

    def test_analog_off_is_zero_and_preserves_manual_percent(self):
        result=self.web.set_heater_mode(self.config,"heater","off")
        self.assertEqual(self.writes(),["/setswaout.cgi?mode=0"])
        self.assertEqual(result["energy"]["heater"]["mode"],"OFF")
        self.assertEqual(self.percent,42)

    def test_relay_off_cancels_timer_and_does_not_touch_other_relay(self):
        result=self.web.set_heater_mode(self.config,"heater2","off")
        self.assertEqual(self.writes(),["/setswrel.cgi?rel=1&state=3&duration=0","/setswrel.cgi?rel=1&state=2"])
        self.assertEqual(result["energy"]["heater2"]["mode"],"OFF")
        self.assertEqual(self.relay2,"0")

    def test_warm_water_all_three_modes_use_only_relay_two(self):
        for mode,expected in (("manual","M"),("off","OFF"),("auto","A")):
            result=self.web.set_heater_mode(self.config,"hot_water",mode)
            self.assertEqual(result["energy"]["hot_water"]["mode"],expected)
        self.assertTrue(all("rel=2" in path for path in self.writes()))
        self.assertEqual(self.relay,"0")

    def test_charger_off_uses_verified_local_code_and_readback(self):
        current=[2]
        paths=[]
        def request(host,path,timeout):
            paths.append(path)
            if path.startswith("/setswcc"):
                current[0]=int(parse_qs(urlsplit(path).query)["mode"][0]);return b"OK"
            return xml(current[0])
        web=SmartfoxWeb(request,allow_undocumented_control=True)
        self.assertEqual(web.set_charger_mode(self.config,"OFF")["mode"],"OFF")
        self.assertIn("/setswcc.cgi?num=0&mode=3&value=100",paths)
        with self.assertRaises(ValueError):web.set_mode(self.config,"OFF")
        for mode in ("A","M"):
            with self.assertRaises(ValueError):web.set_charger_mode(self.config,mode)

    def test_control_requires_gate_firmware_and_exact_installed_mapping(self):
        self.web.allow_heater_control=False
        with self.assertRaises(SmartfoxControlError):self.web.set_heater_mode(self.config,"heater","manual")
        self.web.allow_heater_control=True
        self.firmware="unexpected"
        with self.assertRaises(SmartfoxControlError):self.web.set_heater_mode(self.config,"heater","manual")
        self.firmware="EM3 00.01.16.08"
        for name,kind in (("Other output","0"),("Heizstab 2","3")):
            self.name,self.relay_type=name,kind
            with self.subTest(name=name,kind=kind),self.assertRaises(SmartfoxControlError):
                self.web.set_heater_mode(self.config,"heater2","manual")
        self.assertEqual(self.writes(),[])

    def test_missing_manual_setpoint_never_defaults_to_full_power(self):
        self.percent=""
        with self.assertRaises(SmartfoxControlError):self.web.set_heater_mode(self.config,"heater","manual")
        self.assertEqual(self.writes(),[])

    def test_unconfirmed_readback_is_an_error_and_other_outputs_are_rejected(self):
        self.apply=False
        with self.assertRaises(SmartfoxControlError):self.web.set_heater_mode(self.config,"heater","manual")
        self.assertEqual(len(self.writes()),1)
        for device,mode in (("heater","timer"),("relay4","auto")):
            with self.subTest(device=device),self.assertRaises(ValueError):self.web.set_heater_mode(self.config,device,mode)
        self.assertEqual(len(self.writes()),1)
