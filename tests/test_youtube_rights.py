"""YouTube TV package boundaries: a selected plan must not unlock unpaid add-ons."""
import unittest
from datetime import date
from unittest import mock

import build


YOUTUBE_SERVICES = {"yttv", "yttvsports", "yttvsportsplus", "yttvspanishplus", "yttvspanish"}


class YouTubeRights(unittest.TestCase):
    def assert_access(self, services, channels, available):
        for channel in channels:
            with self.subTest(services=services, channel=channel):
                outlet = build.map_outlet(channel, "mex.1")
                self.assertTrue(outlet.known)
                result = build.evaluate([outlet], None, set(services))
                if available:
                    self.assertIn(result[0], services)
                    self.assertEqual(result[1:], ("listed", outlet.label))
                else:
                    self.assertEqual(result, ("", "none", ""))

    def test_main_and_sports_include_verified_sports_and_espn_unlimited(self):
        for service in ("yttv", "yttvsports"):
            self.assert_access({service}, ["FOX", "FS1", "FS2", "BTN", "USA Net", "NBCSN", "CBSSN",
                                          "TNT", "TBS", "truTV", "ESPN", "ESPN2", "ESPN+",
                                          "ESPN Unlimited", "ESPNU", "ESPNEWS", "ESPN Deportes",
                                          "ACCN", "SECN", "Tele", "TUDN", "Univision", "UniMas"], True)

    def test_main_and_sports_do_not_include_paid_addons_or_other_apps(self):
        for service in ("yttv", "yttvsports"):
            self.assert_access({service}, ["Fox Deportes", "FSP", "beIN Sports", "beIN Sports en Español",
                                          "FOX One", "Peacock", "Paramount+", "HBO Max", "Apple TV"], False)

    def test_sports_plan_does_not_inherit_the_main_plans_extra_channels(self):
        extras = ["CNBC", "Universo", "Galavision", "ION"]
        self.assert_access({"yttv"}, extras, True)
        self.assert_access({"yttvsports"}, extras, False)

    def test_sports_plus_adds_only_premium_channels(self):
        self.assert_access({"yttvsportsplus"}, ["FSP", "beIN Sports", "beIN Sports XTRA"], True)
        self.assert_access({"yttvsportsplus"}, ["FS2", "ESPN+", "Fox Deportes", "beIN Sports en Español"], False)
        # Selecting both packages combines their channels without broadening either alone.
        self.assert_access({"yttv", "yttvsportsplus"}, ["FS2", "ESPN+", "FSP", "beIN Sports"], True)
        self.assert_access({"yttv", "yttvsportsplus"}, ["Fox Deportes", "beIN Sports en Español"], False)

    def test_spanish_plan_and_spanish_addon_are_distinct(self):
        for service in ("yttvspanish", "yttvspanishplus"):
            self.assert_access({service}, ["ESPN Deportes", "Fox Deportes", "beIN Sports en Español",
                                          "TyC Sports", "Universo"], True)
            self.assert_access({service}, ["Telemundo", "FS2", "ESPN", "ESPN+", "FSP", "beIN Sports"], False)
        local_and_cable = ["TUDN", "Univision", "UniMás", "Galavisión"]
        self.assert_access({"yttvspanish"}, local_and_cable, True)
        self.assert_access({"yttvspanishplus"}, local_and_cable, False)

    def test_no_package_implies_bein_connect_or_free_youtube(self):
        # A named linear channel does not establish access to every overflow stream.
        self.assert_access(YOUTUBE_SERVICES, ["beIN Sports Connect", "YouTube"], False)

    def test_unspecified_fox_network_is_not_treated_as_confirmed_package_access(self):
        rule = build.map_outlet("FOX Sports networks", "mex.1")
        self.assertEqual(build.evaluate([], rule, YOUTUBE_SERVICES), ("", "none", ""))

    def test_espn_usual_home_can_use_the_included_espn_plus_entitlement(self):
        with mock.patch.object(build, "TODAY", date(2026, 10, 12)):
            rule = build.usual_home("esp.1")
        self.assertIsNotNone(rule)
        for service in ("yttv", "yttvsports"):
            self.assertEqual(build.evaluate([], rule, {service}), (service, "rule", "ESPN+"))

    def test_default_lineup_selects_only_the_main_youtube_package(self):
        self.assertTrue(YOUTUBE_SERVICES <= set(build.SERVICES))
        self.assertEqual(YOUTUBE_SERVICES.intersection(build.OWNER), {"yttv"})


if __name__ == "__main__":
    unittest.main()
