import tempfile
import time
import unittest
from unittest.mock import patch
from agent import Agent


class PolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.agent = Agent({"games": [], "reserve_mib": 1024, "models": {"small": 4000, "big": 9000}}, directory.name)
        self.agent.inventory = {"small", "big"}
        self.agent.free = 6000
        self.agent.measured = time.monotonic()
        self.enterContext(patch("agent.idle_seconds", return_value=120))

    def test_fit(self):
        self.assertEqual(self.agent.state()["available"], ["small"])

    def test_unavailable(self):
        for key, value in [("game", True), ("user", "tester"), ("gpu_load", 99), ("measured", 0), ("inventory", set())]:
            with self.subTest(key=key), patch.object(self.agent, key, value):
                self.assertEqual(self.agent.state()["available"], [])
        self.agent.pause.touch()
        self.assertEqual(self.agent.state()["reason"], "paused")

