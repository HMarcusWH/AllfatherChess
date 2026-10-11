"""Recovery stream subscriber isolation and global event-stream fencing."""
import unittest
from tests.lichess.stream_sessions import StreamSessions


class StreamSessionTests(unittest.TestCase):
    def test_new_game_stream_receives_full_snapshot_and_not_old_backlog(self):
        s = StreamSessions()
        first = s.subscribe("game", initial={"type": "gameFull", "state": {"moves": ""}})
        self.assertEqual(first.get()["type"], "gameFull")
        s.publish("game", {"type": "gameState", "moves": "e2e4"})
        self.assertEqual(first.get()["moves"], "e2e4")
        next_stream = s.subscribe("game", initial={"type": "gameFull", "state": {"moves": "e2e4"}})
        self.assertEqual(next_stream.get()["state"]["moves"], "e2e4")
        self.assertIsNone(next_stream.get(timeout=0.01))
        s.unsubscribe(first)
        self.assertFalse(next_stream.ended.is_set())

    def test_second_global_event_stream_fences_first(self):
        s = StreamSessions()
        first = s.subscribe("event")
        second = s.subscribe("event")
        self.assertTrue(first.ended.is_set())
        s.publish("event", {"type": "challenge", "challenge": {"id": "x"}})
        self.assertIsNone(first.get(timeout=0.01))
        self.assertEqual(second.get()["type"], "challenge")
        self.assertEqual([x["event"] for x in s.snapshot()].count("subscribe"), 2)

    def test_stream_data_is_copied_per_subscriber(self):
        s = StreamSessions()
        a = s.subscribe("game")
        b = s.subscribe("game")
        payload = {"type": "gameState", "moves": ["e2e4"]}
        s.publish("game", payload)
        payload["moves"].append("e7e5")
        self.assertEqual(a.get()["moves"], ["e2e4"])
        self.assertEqual(b.get()["moves"], ["e2e4"])


if __name__ == "__main__":
    unittest.main()
