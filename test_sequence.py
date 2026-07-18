"""
Test harness for the ARTIQ Sequence Error Detector.

Synthetic @kernel methods exercise SED lane-congestion patterns.
The main guard parses method source (does not run on a core device),
runs the static analyzer, and cross-checks drops with the SED simulator.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from artiq.experiment import EnvExperiment
from artiq.language.core import kernel, delay, delay_mu, parallel, at_mu
from artiq.language.units import us  # noqa: F401  (appears in delay(100*us) source)

from analyzer import analyze_ast_with_stream
from simulator import simulate_event_stream, generate_stream_from_calls


class SequenceTestCases(EnvExperiment):
    def build(self):
        self.core = self.get_device("core")

    # ---- Safe / boundary ----

    @kernel
    def test_eight_events_ok(self):
        # Exactly 8 events at coarse 0 → fills all lanes, no drop.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()

    @kernel
    def test_nine_events_no_delay(self):
        # 9 events at coarse 0 → sequence error on the 9th.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()
        self.ttl8.on()

    @kernel
    def test_nine_events_with_delay(self):
        # 8 at coarse 0, delay_mu(8) → coarse 1, 9th is safe.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()
        delay_mu(8)
        self.ttl8.on()

    @kernel
    def test_subtick_delay_still_congested(self):
        # delay_mu(4) stays in coarse 0 when coarse_tick=8 → still error.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()
        delay_mu(4)
        self.ttl8.on()

    # ---- pulse / mixed outputs ----

    @kernel
    def test_pulse_events(self):
        # One pulse = 2 events; well under 8 lanes.
        self.ttl0.pulse(5)

    @kernel
    def test_four_pulses_ok(self):
        # 4 pulses = 8 events at coarse 0 → OK.
        self.ttl0.pulse(1)
        self.ttl1.pulse(1)
        self.ttl2.pulse(1)
        self.ttl3.pulse(1)

    @kernel
    def test_five_pulses_error(self):
        # 5 pulses = 10 events at coarse 0 → error.
        self.ttl0.pulse(1)
        self.ttl1.pulse(1)
        self.ttl2.pulse(1)
        self.ttl3.pulse(1)
        self.ttl4.pulse(1)

    @kernel
    def test_mixed_on_off_set_error(self):
        # on/off/set all count as output events.
        self.ttl0.on()
        self.ttl1.off()
        self.ttl2.set(1)
        self.ttl3.on()
        self.ttl4.off()
        self.ttl5.set(0)
        self.ttl6.on()
        self.ttl7.off()
        self.ttl8.on()

    # ---- parallel ----

    @kernel
    def test_parallel(self):
        # Flattened: 2 events, safe.
        with parallel:
            self.ttl1.on()
            self.ttl2.on()

    @kernel
    def test_parallel_nine_error(self):
        # Flattened: 9 events at coarse 0 → error.
        with parallel:
            self.ttl0.on()
            self.ttl1.on()
            self.ttl2.on()
            self.ttl3.on()
            self.ttl4.on()
            self.ttl5.on()
            self.ttl6.on()
            self.ttl7.on()
            self.ttl8.on()

    # ---- timeline API / units ----

    @kernel
    def test_delay_with_units(self):
        # Constant folding: 100*us → 100_000 MU → new coarse bucket.
        self.ttl0.on()
        delay(100 * us)
        self.ttl1.on()

    @kernel
    def test_at_mu_new_bucket_ok(self):
        # Jump cursor to a later coarse bucket before next event.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()
        at_mu(16)  # coarse 2 when coarse_tick=8
        self.ttl8.on()

    @kernel
    def test_at_mu_back_to_zero_error(self):
        # Fill lanes, then jump back to t=0 and post another event → error.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()
        self.ttl3.on()
        self.ttl4.on()
        self.ttl5.on()
        self.ttl6.on()
        self.ttl7.on()
        at_mu(0)
        self.ttl8.on()

    # ---- config: fewer lanes ----

    @kernel
    def test_three_events_lanes2(self):
        # With lanes=2, the 3rd event at coarse 0 is an error.
        self.ttl0.on()
        self.ttl1.on()
        self.ttl2.on()


def _extract_function_ast(func) -> ast.AST:
    """Parse one method; unwrap @kernel; dedent class indentation."""
    target = inspect.unwrap(func)
    src = textwrap.dedent(inspect.getsource(target))
    module = ast.parse(src)
    if len(module.body) == 1 and isinstance(module.body[0], ast.FunctionDef):
        return ast.Module(body=module.body[0].body, type_ignores=[])
    return module


def _run_helper_checks() -> None:
    """Sanity-check generate_stream_from_calls(coarse_tick=...)."""
    assert generate_stream_from_calls([("on", 0), ("on", 8)], coarse_tick=8) == [0, 1]
    assert generate_stream_from_calls([("on", 0), ("on", 8)], coarse_tick=4) == [0, 2]
    assert generate_stream_from_calls([("pulse", 0)], coarse_tick=8) == [0, 0]
    print("helper checks: OK")


def main() -> None:
    # (method_name, expect_sequence_error, analyze/sim kwargs)
    cases = [
        ("test_eight_events_ok", False, {}),
        ("test_nine_events_no_delay", True, {}),
        ("test_nine_events_with_delay", False, {}),
        ("test_subtick_delay_still_congested", True, {}),
        ("test_pulse_events", False, {}),
        ("test_four_pulses_ok", False, {}),
        ("test_five_pulses_error", True, {}),
        ("test_mixed_on_off_set_error", True, {}),
        ("test_parallel", False, {}),
        ("test_parallel_nine_error", True, {}),
        ("test_delay_with_units", False, {}),
        ("test_at_mu_new_bucket_ok", False, {}),
        ("test_at_mu_back_to_zero_error", True, {}),
        ("test_three_events_lanes2", True, {"lanes": 2}),
    ]

    _run_helper_checks()

    failed = 0
    for name, expect_error, opts in cases:
        method = getattr(SequenceTestCases, name)
        tree = _extract_function_ast(method)
        lanes = opts.get("lanes", 8)
        coarse_tick = opts.get("coarse_tick", 8)

        warnings, stream = analyze_ast_with_stream(
            tree, lanes=lanes, coarse_tick=coarse_tick
        )
        dropped = simulate_event_stream(stream, lanes=lanes)

        static_hit = bool(warnings)
        sim_hit = bool(dropped)
        ok = (static_hit == expect_error) and (sim_hit == expect_error)

        print(f"\n=== {name} ===")
        print(f"  expect_error={expect_error}  opts={opts}")
        print(f"  event stream (coarse): {stream}")
        if warnings:
            for w in warnings:
                print(f"  [static] {w}")
        else:
            print("  [static] No sequence errors detected.")
        if dropped:
            print(f"  [sim] dropped: {dropped}")
        else:
            print("  [sim] No drops.")

        if not ok:
            failed += 1
            print(
                f"  FAIL: static={static_hit} sim={sim_hit} "
                f"expected={expect_error}"
            )
        else:
            print("  PASS")

    print(f"\n=== summary: {len(cases) - failed}/{len(cases)} passed ===")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()