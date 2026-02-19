"""Property-based tests for julabo_control.schedule using hypothesis."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from julabo_control.schedule import ScheduleStep, SetpointSchedule


def _make_schedule(steps: list[tuple[float, float]]) -> SetpointSchedule:
    """Build a schedule from (elapsed_minutes, temperature) pairs, deduplicating times."""
    sorted_steps = sorted(steps, key=lambda s: s[0])
    # Deduplicate elapsed_minutes (keep first occurrence)
    seen: set[float] = set()
    unique: list[tuple[float, float]] = []
    for m, t in sorted_steps:
        if m not in seen:
            seen.add(m)
            unique.append((m, t))
    if not unique:
        unique = sorted_steps[:1]
    return SetpointSchedule(
        steps=[ScheduleStep(elapsed_minutes=m, temperature=t) for m, t in unique]
    )


# Strategy: 1-10 schedule steps with reasonable values
schedule_steps_strategy = st.lists(
    st.tuples(
        st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-50.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    ),
    min_size=1,
    max_size=10,
)

# Strategy for round-trip tests: unique elapsed_minutes
unique_schedule_steps_strategy = st.lists(
    st.tuples(
        st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=-50.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    ),
    min_size=1,
    max_size=10,
    unique_by=lambda x: round(x[0], 2),  # unique at CSV precision
)


class TestSetpointAtProperties:
    @given(
        steps=schedule_steps_strategy,
        elapsed=st.floats(
            min_value=-100.0, max_value=2000.0,
            allow_nan=False, allow_infinity=False,
        ),
    )
    @settings(max_examples=200)
    def test_always_returns_float(
        self, steps: list[tuple[float, float]], elapsed: float
    ) -> None:
        schedule = _make_schedule(steps)
        result = schedule.setpoint_at(elapsed)
        assert isinstance(result, float)

    @given(steps=schedule_steps_strategy)
    @settings(max_examples=200)
    def test_before_first_equals_first(
        self, steps: list[tuple[float, float]]
    ) -> None:
        schedule = _make_schedule(steps)
        first_temp = schedule.steps[0].temperature
        first_time = schedule.steps[0].elapsed_minutes
        result = schedule.setpoint_at(first_time - 100.0)
        assert result == first_temp

    @given(steps=schedule_steps_strategy)
    @settings(max_examples=200)
    def test_after_last_equals_last(
        self, steps: list[tuple[float, float]]
    ) -> None:
        schedule = _make_schedule(steps)
        last_temp = schedule.steps[-1].temperature
        last_time = schedule.steps[-1].elapsed_minutes
        result = schedule.setpoint_at(last_time + 100.0)
        assert result == last_temp

    @given(
        t1=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        temp1=st.floats(min_value=-50.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        delta_t=st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
        temp2=st.floats(min_value=-50.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_interpolation_bounded(
        self,
        t1: float,
        temp1: float,
        delta_t: float,
        temp2: float,
        frac: float,
    ) -> None:
        """Interpolated value is always between the two step temperatures."""
        t2 = t1 + delta_t
        schedule = SetpointSchedule(
            steps=[
                ScheduleStep(elapsed_minutes=t1, temperature=temp1),
                ScheduleStep(elapsed_minutes=t2, temperature=temp2),
            ]
        )
        elapsed = t1 + frac * delta_t
        result = schedule.setpoint_at(elapsed)
        lo = min(temp1, temp2)
        hi = max(temp1, temp2)
        assert lo - 1e-9 <= result <= hi + 1e-9


class TestScheduleRoundTripCSV:
    @given(steps=unique_schedule_steps_strategy)
    @settings(max_examples=50)
    def test_save_then_load_round_trip(
        self, steps: list[tuple[float, float]], tmp_path_factory
    ) -> None:
        schedule = _make_schedule(steps)
        path = tmp_path_factory.mktemp("sched") / "schedule.csv"
        schedule.save_csv(path)
        loaded = SetpointSchedule.load_csv(path)
        assert len(loaded.steps) == len(schedule.steps)
        for orig, loaded_step in zip(schedule.steps, loaded.steps):
            assert abs(orig.elapsed_minutes - loaded_step.elapsed_minutes) < 0.01
            assert abs(orig.temperature - loaded_step.temperature) < 0.01
