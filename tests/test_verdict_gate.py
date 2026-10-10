"""10-10 f verdict gate: planned R:R >= 2 drops the win-rate condition; n >= 100 and HOLDOUT CI95 lower > 0 stay."""
from tools.intraday_replay import RR_GATE, VARIANTS, eligible, planned_rr


def h(n=150, lo=0.5, win=0.34):
    return {'n': n, 'ci95': [lo, lo + 5], 'win_rate': win}


def test_planned_rr_of_the_registered_variants():
    assert planned_rr(VARIANTS['P15_rr25']) == 2.5
    assert planned_rr(VARIANTS['P15_rr25atr']) == 2.5
    assert planned_rr(VARIANTS['P15_tp50']) is None        # TP1 + trail: no fixed R:R
    assert planned_rr(None) is None and RR_GATE == 2.0


def test_high_rr_passes_on_ci_alone():
    assert eligible(h(win=0.34), VARIANTS['P15_rr25atr'])
    assert not eligible(h(win=0.34), VARIANTS['P15_tp50'])  # R:R < 2 keeps win > 50 %
    assert eligible(h(win=0.55), VARIANTS['P15_tp50'])
    assert eligible(h(win=0.55), None) and not eligible(h(win=0.45), None)   # burst variants: not in VARIANTS


def test_n_and_ci_floor_hold_for_every_variant():
    v = VARIANTS['P15_rr25atr']
    assert not eligible(h(n=99), v)
    assert not eligible(h(lo=0.0), v)
    assert not eligible(h(lo=-0.1, win=0.9), v)
    assert not eligible({'n': 0}, v)


def test_universe_filter_keeps_only_months_the_symbol_was_screened_in():
    from tools.intraday_replay import in_universe
    jan, feb = 1735689600, 1738368000                          # 2025-01-01, 2025-02-01 00:00 UTC
    tr = [{'t': jan + 60}, {'t': feb - 60}, {'t': feb + 60}]
    assert in_universe(tr, {'2025-01'}) == tr[:2]
    assert in_universe(tr, set()) == []
