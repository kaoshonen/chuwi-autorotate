import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chuwi_tablet_client import TabletClient


class ActivationCancellationTests(unittest.TestCase):
    def setUp(self):
        self.client=TabletClient.__new__(TabletClient)
        self.client.closed=False
        self.client.activation=1
        self.client.starting=True
        self.client.present=True
        self.client.state={'phase':'arming'}
        self.client.on_ready=Mock()
        self.client.on_state=Mock()
        self.client.call=Mock()

    def test_unlock_cancels_delayed_start_before_it_can_disable_input(self):
        self.client.unlock()
        self.client.ready(1)
        self.client.begin()
        self.client.on_ready.assert_not_called()
        self.client.call.assert_called_once_with('Unlock')

    def test_old_start_completion_cannot_activate_a_new_attempt(self):
        self.client.activation=2
        self.client.ready(1)
        self.client.on_ready.assert_not_called()
        self.assertTrue(self.client.starting)
        self.client.ready(2)
        self.client.on_ready.assert_called_once()

    def test_closing_panel_cancels_pending_begin(self):
        self.client.closed=True
        self.client.ready(1)
        self.client.begin()
        self.client.on_ready.assert_not_called()
        self.client.call.assert_not_called()

    def test_stale_start_failure_cannot_stop_a_later_activation(self):
        self.client.activation=2
        self.client.emergency_unlock=Mock()
        self.client.start_failed('Old service error',1)
        self.client.emergency_unlock.assert_not_called()


if __name__=='__main__':unittest.main()
