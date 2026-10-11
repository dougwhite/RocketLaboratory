import copy
import unittest
from rocketlaboratory.instrument import tape


class TapeTests(unittest.TestCase):
    def spec(self):
        return {
            "schema": "noodle.instrument.tape.v1",
            "duration_steps": 3,
            "actors": {
                "1": [
                    {"start_step": 0, "duration_steps": 1, "controls": {"jump": True}}
                ],
                "2": [
                    {
                        "start_step": 1,
                        "duration_steps": 2,
                        "controls": {"throttle": 0.125},
                    }
                ],
            },
        }

    def test_independent_actors_neutral_gaps(self):
        rows = tape.compile_tape(self.spec()).splitlines()
        self.assertEqual(rows[0], "NNIT1 3 2")
        self.assertEqual(rows[2].split()[8], "1")
        self.assertEqual(rows[3], "0 0 0 0 0 0 0 0 0 0 0 0")
        self.assertEqual(rows[7].split()[0], "0.125")

    def test_invalid(self):
        original = self.spec()
        for controls in [
            {"throttle": float("nan")},
            {"throttle": 10**400},
            {"jumped": True},
            {"jump": 1},
            {"roll": 1.001},
            {"teleport": True},
        ]:
            s = copy.deepcopy(original)
            s["actors"]["1"][0]["controls"] = controls
            with self.assertRaises(ValueError):
                tape.compile_tape(s)
        s = self.spec()
        s["actors"]["1"] *= 2
        with self.assertRaises(ValueError):
            tape.compile_tape(s)

    def test_bounds(self):
        for duration in [0, True, 72001, 2.5]:
            s = self.spec()
            s["duration_steps"] = duration
            with self.assertRaises(ValueError):
                tape.compile_tape(s)


if __name__ == "__main__":
    unittest.main()
