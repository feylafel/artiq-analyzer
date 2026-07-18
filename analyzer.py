'''
Static analyzer for ARTIQ Sequence Errors (SED Lane Congestion).

Real-time model (ARTIQ docs + Riesebos & Brown, arXiv:2210.14364):
- Timeline cursor is in machine units (MU), typically 1 MU = 1 ns.
- RTIO counter increments every nanosecond (paper §III-A).
- SED coarse buckets are often t_mu // 8 on default configs (configurable).
- Default SED lanes = 8 (board-dependent, configurable).
- Zero-duration outputs (on/off/set) do not advance the cursor.
- pulse: modeled as ON+OFF at the current cursor (conservative for lanes).
'''

from __future__ import annotations

import ast
from typing import List, Optional, Tuple


# ARTIQ SI time units (seconds), as used in delay(100*us).
_TIME_UNITS_S = {
    "s": 1.0,
    "ms": 1e-3,
    "us": 1e-6,
    "ns": 1e-9,
}


class SequenceErrorAnalyzer(ast.NodeVisitor):
    def __init__(self, lanes: int = 8, coarse_tick: int = 8, mu_per_second: float = 1e9):
        '''
        Args:
            lanes: Number of SED lanes (default 8).
            coarse_tick: MU per coarse SED timestamp (default 8).
            mu_per_second: MU per wall-clock second (default 1e9 => 1 MU = 1 ns).
        '''
        self.lanes = lanes
        self.coarse_tick = coarse_tick
        self.mu_per_second = mu_per_second

        self.current_time_mu = 0
        self.current_coarse = 0
        self.event_count = 0

        self.event_stream: List[int] = []
        self.warnings: List[str] = []

    def _record_event(self, node: ast.AST) -> None:
        coarse_ts = self.current_time_mu // self.coarse_tick

        # DEBUG (disabled by default):
        # print(
        #     f"EVENT line={getattr(node, 'lineno', '?')} "
        #     f"t_mu={self.current_time_mu} coarse={coarse_ts} "
        #     f"count_before={self.event_count}"
        # )

        if coarse_ts != self.current_coarse:
            self.current_coarse = coarse_ts
            self.event_count = 1
        else:
            self.event_count += 1

        self.event_stream.append(coarse_ts)

        if self.event_count > self.lanes:
            self.warnings.append(
                f"SEQUENCE ERROR (Lane Congestion) at line {node.lineno}: "
                f"{self.event_count} events in coarse bucket {coarse_ts}. "
                f"Event #{self.event_count} will be dropped by the SED."
            )

    def _eval_numeric(self, node: ast.AST) -> Optional[float]:
        '''
        Constant-fold simple numeric / time-unit expressions.
        Returns a Python float, or None if unknown.
        '''
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)

        if isinstance(node, ast.Name) and node.id in _TIME_UNITS_S:
            return _TIME_UNITS_S[node.id]

        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = self._eval_numeric(node.operand)
            if v is None:
                return None
            return v if isinstance(node.op, ast.UAdd) else -v

        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = self._eval_numeric(node.left)
            right = self._eval_numeric(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                return None
            return left / right

        return None

    def _eval_delay_mu(self, arg: ast.AST, *, is_mu: bool) -> int:
        '''
        Evaluate delay(...) / delay_mu(...) into MU.

        - delay_mu(x): x is already MU (prefer int).
        - delay(x): x is seconds (e.g. 100*us) -> MU via mu_per_second.

        Fallback: advance by one coarse tick so the cursor cannot silently stall.
        '''
        value = self._eval_numeric(arg)
        if value is None:
            return self.coarse_tick # might be too conservative

        if is_mu:
            return int(value)

        # seconds -> MU (paper: delay(d) converts seconds to MU)
        return int(value * self.mu_per_second)

    def visit_Call(self, node: ast.Call) -> None:
        # delay / delay_mu: advance cursor only
        if isinstance(node.func, ast.Name) and node.func.id in ("delay", "delay_mu"):
            if node.args:
                is_mu = node.func.id == "delay_mu"
                self.current_time_mu += self._eval_delay_mu(node.args[0], is_mu=is_mu)
            return

        # at_mu(t): absolute cursor move (paper §IV-A). Relative to lane
        if isinstance(node.func, ast.Name) and node.func.id == "at_mu":
            if node.args:
                v = self._eval_numeric(node.args[0])
                if v is not None:
                    self.current_time_mu = int(v)
                else:
                    self.current_time_mu += self.coarse_tick
            return

        if isinstance(node.func, ast.Attribute):
            method = node.func.attr

            if method == "pulse":
                # Two events at the current timestamp (lane-oriented model). WORSTCASE
                self._record_event(node)
                self._record_event(node)
                return

            if method in ("on", "off", "set"):
                self._record_event(node)
                return

        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        '''
        - CORRECT hardware behavior: delays in `with parallel:` take MAX duration, not sum.
        - Events inside a parallel block are scheduled relative to the block's entry time.
        - CURRENT IMPLEMENTATION: Sequentially flattens parallel bodies (visits statements one after another).
          This takes the SUM of delays, which pushes events into later coarse buckets.
          This is UNSOUND because it can hide real congestion (false negatives).
        - TO FIX (planned): Implement the paper's sequential/parallel context stack.
          This will correctly schedule events at the block's start time and advance the cursor by the max delay on exit.
        '''
        for item in node.body:
            self.visit(item)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        pass

def analyze_ast(
    tree: ast.AST,
    lanes: int = 8,
    coarse_tick: int = 8,
) -> List[str]:
    analyzer = SequenceErrorAnalyzer(lanes=lanes, coarse_tick=coarse_tick)
    analyzer.visit(tree)
    return analyzer.warnings


def analyze_ast_with_stream(
    tree: ast.AST,
    lanes: int = 8,
    coarse_tick: int = 8,
) -> Tuple[List[str], List[int]]:
    analyzer = SequenceErrorAnalyzer(lanes=lanes, coarse_tick=coarse_tick)
    analyzer.visit(tree)
    return analyzer.warnings, analyzer.event_stream