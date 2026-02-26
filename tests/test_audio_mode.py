from __future__ import annotations

import unittest
from unittest import mock

from partybox.audio_mode import AudioModeManager


class TestAudioModeManager(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = {
            "media_mode": "partybox",
            "av_mode": "partybox",
            "tv_paused": "0",
            "tv_muted": "0",
            "media_mode_last_switch_ts": "0",
            "media_mode_last_error": "",
            "media_mode_last_actions_json": "[]",
        }

        def fake_get_setting(k: str, default=None):
            return self.settings.get(k, default)

        def fake_set_setting(k: str, v: str):
            self.settings[k] = v

        self.p_get = mock.patch("partybox.audio_mode.DB.get_setting", side_effect=fake_get_setting)
        self.p_set = mock.patch("partybox.audio_mode.DB.set_setting", side_effect=fake_set_setting)
        self.p_get.start()
        self.p_set.start()
        self.addCleanup(self.p_get.stop)
        self.addCleanup(self.p_set.stop)

    def _manager(self) -> AudioModeManager:
        mgr = AudioModeManager()
        mgr._service_states = mock.Mock(return_value={})  # type: ignore[method-assign]
        mgr._audio_muted = mock.Mock(return_value=False)  # type: ignore[method-assign]
        mgr._bluetooth_connected_devices = mock.Mock(return_value=[])  # type: ignore[method-assign]
        return mgr

    def test_rejects_invalid_mode(self) -> None:
        mgr = self._manager()
        out = mgr.set_media_mode("not-a-mode")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "bad mode")

    def test_noop_when_mode_already_active(self) -> None:
        mgr = self._manager()
        out = mgr.set_media_mode("partybox")
        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "partybox")
        self.assertTrue(out["status"]["noop"])

    def test_persists_mode_on_successful_switch(self) -> None:
        mgr = self._manager()
        mgr._apply_mode = mock.Mock(return_value=None)  # type: ignore[method-assign]
        out = mgr.set_media_mode("spotify", force=True)
        self.assertTrue(out["ok"])
        self.assertEqual(self.settings["media_mode"], "spotify")
        self.assertEqual(self.settings["av_mode"], "spotify")
        self.assertEqual(self.settings["tv_paused"], "1")

    def test_switching_away_from_bluetooth_tears_down_bluetooth_stack(self) -> None:
        self.settings["media_mode"] = "bluetooth"
        mgr = self._manager()
        mgr._set_sink_mute = mock.Mock(return_value={"ok": True})  # type: ignore[method-assign]

        systemctl_calls = []

        def fake_systemctl(action: str, unit: str, tolerate_missing: bool = True):
            systemctl_calls.append((action, unit, tolerate_missing))
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        mgr._systemctl = mock.Mock(side_effect=fake_systemctl)  # type: ignore[method-assign]
        mgr._wait_for_service_active = mock.Mock(  # type: ignore[method-assign]
            return_value={"ok": True, "status": "active", "stderr": ""}
        )

        bt_calls = []

        def fake_bt(*args: str, timeout: float = 2.5):
            bt_calls.append(args)
            if args == ("devices", "Connected"):
                return {
                    "ok": True,
                    "stdout": "Device B4:55:75:BA:A9:A1 Jphone",
                    "stderr": "",
                    "returncode": 0,
                }
            return {"ok": True, "stdout": "", "stderr": "", "returncode": 0}

        mgr._bluetoothctl_single = mock.Mock(side_effect=fake_bt)  # type: ignore[method-assign]

        out = mgr.set_media_mode("partybox")
        self.assertTrue(out["ok"])
        self.assertEqual(self.settings["media_mode"], "partybox")

        self.assertIn(("disconnect", "B4:55:75:BA:A9:A1"), bt_calls)
        self.assertIn(("power", "off"), bt_calls)
        self.assertIn(("stop", "bluetooth.service", True), systemctl_calls)


if __name__ == "__main__":
    unittest.main()
