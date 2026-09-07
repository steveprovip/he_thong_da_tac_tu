from core.models import Filters, dec
from core.position_sizer import PositionSizer, DrawdownBrake

FILTERS = Filters(*map(dec, ['.1', '.001', '.001', '100', '100']))


def test_kelly_warmup_negative_edge_and_window():
    s = PositionSizer()
    assert s.kelly() is None
    assert s.size(10000, 0, 60000, 59000, FILTERS).reason == 'KELLY_WARMUP'
    for i in range(210):
        s.record(-1)
    assert len(s.outcomes) == 200 and s.kelly() == 0
    assert s.size(10000, 0, 60000, 59000, FILTERS).quantity == '0'


def test_round_up_minimum_without_breaking_caps():
    s = PositionSizer()
    result = s.size(1000, 0, 60000, 59000, FILTERS, bootstrap=True)
    assert result.quantity == '0.002'
    assert result.notional == 120
    assert s.size(10, 0, 60000, 59000, FILTERS, bootstrap=True).quantity == '0'
    assert s.size(1000, 749, 60000, 59000, FILTERS, bootstrap=True).quantity == '0'


def test_fractional_kelly_and_latched_brake():
    # min_samples is pinned here so this stays a test of the Kelly formula, not
    # of the sample-size policy (which test_edge_verdict_states covers).
    s = PositionSizer(min_samples=20)
    for value in [2]*12+[-1]*8:
        s.record(value)
    assert abs(s.kelly()-.10) < 1e-10
    brake = DrawdownBrake()
    assert not brake.update(100)
    assert brake.update(90)
    assert brake.update(110)


# ------------------------------------------------- three-state edge lifecycle
def test_bootstrap_sizing_survives_a_short_negative_sample():
    """Regression: 20 noisy settled outcomes must not lock the bot out entirely.

    kelly() previously returned 0.0 (not None) as soon as len(outcomes) reached
    min_samples, so size() hit NONPOSITIVE_EDGE and the bootstrap branch became
    unreachable -- the venue reported authorized=0 with NONPOSITIVE_KELLY on
    99.8% of symbol-cycles and no further evidence could ever be collected. An
    undetermined edge must keep collecting at the fixed bootstrap fraction.
    """
    s = PositionSizer()
    for value in [-0.0019, 0.0007, -0.0003, -0.0014, 0.0004, -0.0010, -0.0012,
                  0.0012, -0.0024, 0.0008, -0.0006, -0.0038, -0.0011, -0.0024,
                  0.0020, -0.0013, -0.0031, -0.0008, -0.0021, 0.0019]:
        s.record(value)
    assert len(s.outcomes) == 20
    assert s.edge_verdict() == 'WARMUP'
    assert s.kelly() is None, 'an uncalibrated sample is not a proven-zero edge'
    result = s.size(4930, 0, 12.0, 11.9, FILTERS, bootstrap=True, available=4930,
                    bootstrap_fraction=.01)
    assert result.reason == 'OK' and result.notional > 0


def test_edge_verdict_states():
    warm = PositionSizer()
    for _ in range(59):
        warm.record(.01)
    assert warm.edge_verdict() == 'WARMUP' and warm.kelly() is None

    positive = PositionSizer()
    for _ in range(60):
        positive.record(.01)
    assert positive.edge_verdict() == 'POSITIVE'
    assert positive.kelly() > 0 and positive.edge_interval()[0] > 0

    # Symmetric noise: the mean wanders but the interval never clears zero, so
    # the sizer must keep collecting instead of promoting noise to a Kelly bet.
    undetermined = PositionSizer()
    for i in range(80):
        undetermined.record(.01 if i % 2 else -.01)
    low, high = undetermined.edge_interval()
    assert low <= 0 <= high
    assert undetermined.edge_verdict() == 'UNDETERMINED'
    assert undetermined.kelly() is None


def test_negative_verdict_requires_stop_samples_and_then_halts():
    """A proven-negative edge stops; it does not merely shrink."""
    s = PositionSizer()
    for _ in range(119):
        s.record(-.01)
    assert s.edge_interval()[1] < 0
    assert s.edge_verdict() == 'UNDETERMINED', 'below stop_samples the run continues'
    assert s.kelly() is None
    s.record(-.01)
    assert len(s.outcomes) == 120 and s.edge_verdict() == 'NEGATIVE'
    assert s.kelly() == 0
    assert s.size(4930, 0, 12.0, 11.9, FILTERS, bootstrap=True,
                  bootstrap_fraction=.01).reason == 'NONPOSITIVE_EDGE'


def test_edge_interval_is_deterministic_and_invalidated_on_record():
    a, b = PositionSizer(), PositionSizer()
    for value in [.01, -.02, .03, -.01, .02] * 14:
        a.record(value)
        b.record(value)
    assert a.edge_interval() == b.edge_interval(), 'seeded bootstrap must be reproducible'
    before = a.edge_interval()
    a.record(.5)
    assert a.edge_interval() != before, 'the cache must not outlive a new outcome'


def test_mainnet_path_still_refuses_an_uncalibrated_edge():
    """The bootstrap branch is testnet-only; mainnet needs a measured edge."""
    s = PositionSizer()
    for i in range(80):
        s.record(.01 if i % 2 else -.01)
    assert s.edge_verdict() == 'UNDETERMINED'
    assert s.size(4930, 0, 12.0, 11.9, FILTERS, bootstrap=False).reason == 'KELLY_WARMUP'


def test_sizing_configuration_is_validated():
    import pytest
    with pytest.raises(ValueError):
        PositionSizer(stop_samples=10, min_samples=60)
    with pytest.raises(ValueError):
        PositionSizer(ci_draws=10)
