'''
Dynamic SED (Scalable Event Dispatcher) Lane Simulator.

This simulates the actual hardware placement of events into lanes.
It takes a stream of coarse timestamps and tries to place each event into
a lane where the last timestamp is strictly less than the new timestamp.
If all lanes are blocked -> Sequence Error occurs.
'''

from typing import List, Tuple

def simulate_event_stream(stream: List[int], lanes: int = 8) -> List[int]:
    """
    Simulate SED placement for a stream of coarse timestamps.

    Args:
        stream: List of coarse timestamps (in coarse ticks, e.g., 8ns units).
        lanes: Number of available SED lanes (default 8).

    Returns:
        A list of coarse timestamps that were DROPPED (sequence errors).
    """
    # Each lane stores the last coarse timestamp placed in it. -1 means empty.
    lane_last = [-1] * lanes
    dropped = []

    for idx, coarse_ts in enumerate(stream):
        placed = False
        # Try to place the event in the first lane where lane_last < coarse_ts.
        for i in range(lanes):
            if lane_last[i] < coarse_ts:
                lane_last[i] = coarse_ts
                placed = True
                break

        if not placed:
            # All lanes contain a timestamp >= coarse_ts.
            dropped.append(coarse_ts)
            # In hardware, the event is silently discarded and execution continues.
            # We do not update lanes for the dropped event.
            print(f"SIMULATION: Sequence Error at event #{idx+1}! "
                  f"Coarse {coarse_ts} dropped. Lanes: {lane_last}")

    return dropped


def generate_stream_from_calls(
    calls: List[Tuple[str, int]],
    coarse_tick: int = 8,
) -> List[int]:
    '''
    Convert a high-level list of function calls into a coarse timestamp stream.
    Args:
        calls: List of (function_name, fine_timestamp_mu),
               e.g. [("on", 0), ("pulse", 0), ("on", 8)].
        coarse_tick: MU per coarse SED timestamp (must match the analyzer).
    Returns:
        Coarse timestamps (fine_timestamp // coarse_tick) for each output event.
    '''
    stream = []
    for name, ts in calls:
        coarse = ts // coarse_tick
        if name == "pulse":
            stream.append(coarse)
            stream.append(coarse)
        elif name in ("on", "off", "set"):
            stream.append(coarse)
        # delay / at_mu do not emit output events here
    return stream