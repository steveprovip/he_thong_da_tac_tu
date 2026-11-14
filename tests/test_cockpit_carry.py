"""11-07 (d) owner cockpit request: the modeled edge floor on chop scalps, the simulated dual-leg carry book
(research/carry_lane.CarrySim) inside Runner.funding_loop, and static checks of the single-screen cockpit (web_ui/).

Behaviour tests only. The carry entry rule is the unreplayed RFC threshold (2.5 bps/8 h, APR >= 20 %); the replay
reference is 1.90 %/yr realized (docs/harness/progress.md), so nothing here is evidence of a positive expectancy."""
import re
import shutil
import subprocess
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import engine as eng
from core import funding_executor as fx
from core import invariants as inv
from research import carry_lane as cl
from tests.test_chop_burst_gating_2026_10_16 import BN, HL
from tests.test_continuous_active_trading import BUY, DIAG, SCALP_F, orders
from tests.test_two_mode_engine import fill_through, mock_engine

WEB = Path(__file__).resolve().parents[1] / 'web_ui'
LINK = fx.FundingQuote('LINK', 0.0001, 0.00005, 20.0, 20.01)     # BN 1 bps/8 h, HL 4 bps/8 h, basis +5 bps
LINK_FLIP = fx.FundingQuote('LINK', 0.0006, 0.00005, 20.0, 20.01)  # BN 6 bps/8 h > HL 4: the spread flipped
LINK_UNKNOWN = fx.FundingQuote('LINK', None, None, None, None)


# ============================================================================ scalp edge floor
async def test_scalp_needs_18_bps_modeled_edge_else_edge_too_low_for_fees(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', qty=0.004, scalp_min_edge_bps=18.0)
    await e.sync()
    f = {**feed.features(), **SCALP_F}                            # OFI +0.6 -> a BUY scalp setup, BUY 80 % vote
    e.diag = None                                                 # no swarm diagnostics: the edge is UNKNOWN
    await e.chop_scalp(BUY, f)
    e.scalp_next, e.diag = 0.0, {**DIAG, 'edge_bps': 12.0}        # modeled 12 bps < 18
    await e.chop_scalp(BUY, f)
    assert e.pos is None and e.authorized == 0 and orders(tmp_path) == [] and not venue.orders
    assert e.funnel.outcomes['EDGE_LOW'] == 2
    msgs = [x['message'] for x in e.events]
    assert any(m.startswith('EDGE TOO LOW FOR FEES: SCALP BUY edge UNKNOWN bps < 18 bps') for m in msgs)
    e.scalp_next, e.diag = 0.0, {**DIAG, 'edge_bps': 25.0}
    await fill_through(e, feed, e.chop_scalp(BUY, f))             # 25 bps >= 18: the VESKA-tokened scalp opens
    assert e.pos and e.pos['path'] == 'SCALP' and e.authorized == 1


@pytest.mark.production_profile
def test_shipped_profiles_floor_the_scalp_edge_and_simulate_carry_in_demo_only():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.scalp_min_edge_bps == 10.0 and live.scalp_min_edge_bps == 18.0 and d.min_edge_bps >= 18.0
    assert d.carry_sim and not live.carry_sim and not eng.Profile('x', 'BTCUSDT').carry_sim
    assert (inv.SUPERMAJORITY, inv.VETO) == (0.768, 0.70)         # quorum and RUNE veto untouched


async def test_demo_scalp_at_12_2_bps_clears_the_ten_bps_edge_floor(tmp_path):
    e, venue, feed = mock_engine(tmp_path, chop_scalper='on', qty=0.004, scalp_min_edge_bps=10.0,
                                 scalp_net_rr=True, net_rr_min=1.30)
    await e.sync()
    f = {**feed.features(), **SCALP_F}
    e.diag = {**DIAG, 'edge_bps': 12.2}

    await fill_through(e, feed, e.chop_scalp(BUY, f))

    assert e.pos and e.pos['path'] == 'SCALP' and e.pos['rr_net'] >= 1.30
    assert e.authorized == 1 and e.funnel.outcomes['EDGE_LOW'] == 0


# ============================================================================ simulated carry book
async def test_carry_sim_opens_bn_post_only_and_hl_ioc_accrues_and_exits_on_flip():
    t = [1_000_000.0]
    sim = cl.CarrySim(1000.0, clock=lambda: t[0])
    v = await sim.step([LINK])
    p = sim.pairs['LINK']
    assert (p['short'], p['long']) == ('HL', 'BN') and [x['coin'] for x in v['positions']] == ['LINK']
    assert [c[:3] for c in p['bn'].calls] == [('post_only', 'LINKUSDT', 'BUY'),         # maker leg, Binance,
                                              ('fill', 'LINKUSDT', 'BUY')]              # resting bid filled
    assert [c[:3] for c in p['hl'].calls] == [('ioc', 'LINK', 'SELL'),                 # IOC hedge, Hyperliquid
                                              ('fill', 'LINK', 'SELL')]
    assert v['mode'].startswith('SIMULATED') and v['rule']['validated'] is False
    assert any(m.startswith('CARRY SIM OPEN LINK') for m in v['new_events'])
    qty = sim.notional_per_leg / 20.0
    t[0] += 3600.0
    v = await sim.step([LINK])                                    # 1 h: HL short receives, BN long pays
    assert v['positions'][0]['funding_usdt'] == pytest.approx(qty * (20.01 * 5e-5 - 20.0 * 1e-4 / 8), abs=1e-4)
    t[0] += 300.0
    v = await sim.step([LINK_FLIP])
    assert 'LINK' not in sim.pairs and v['positions'] == [] and sim.closed[-1]['coin'] == 'LINK'
    assert 'flipped' in sim.closed[-1]['why'] and any(m.startswith('CARRY SIM CLOSE LINK') for m in v['new_events'])


async def test_carry_sim_unknown_quote_holds_and_the_kill_switch_blocks_new_pairs():
    t = [1_000_000.0]
    sim = cl.CarrySim(1000.0, clock=lambda: t[0])
    await sim.step([LINK])
    t[0] += 3600.0
    v = await sim.step([LINK_UNKNOWN])                            # UNKNOWN is never read as a flip, nothing accrues
    assert 'LINK' in sim.pairs and v['positions'][0]['funding_usdt'] == 0.0
    assert v['top'] == [{'coin': 'LINK', 'status': 'UNKNOWN', 'why': v['top'][0]['why']}]
    halted = cl.CarrySim(1000.0, clock=lambda: t[0])
    halted.kill.halt('test trip')
    v = await halted.step([LINK])
    assert halted.pairs == {} and v['kill'].startswith('HALTED') and v['top'][0]['status'] == 'TARGET'


def funding_quote(coin, hl_rate, bn_rate=0.0001, bn_mark=20.0, hl_mark=20.01):
    return fx.FundingQuote(coin, bn_rate, hl_rate, bn_mark, hl_mark)


async def test_carry_rotation_hourly_selects_top_net_apr_and_prefers_pepe_wld_on_ties():
    t = [1_000_000.0]
    sim = cl.CarrySim(1000.0, clock=lambda: t[0])
    first = [funding_quote('LINK', 0.00005), funding_quote('WLD', 0.00010),
             funding_quote('ETH', 0.00010), funding_quote('1000PEPE', 0.00010)]
    v = await sim.step(first)
    assert v['rotation']['targets'] == ['1000PEPE', 'WLD', 'ETH']
    assert v['rotation']['interval_s'] == 3600.0
    assert [p['coin'] for p in v['positions']] == ['1000PEPE', 'WLD', 'ETH']
    assert v['top'][0]['net_apr_pct'] == pytest.approx(v['top'][0]['apr_pct'])

    t[0] += 3600.0
    second = [funding_quote('LINK', 0.00020), funding_quote('WLD', 0.00010),
              funding_quote('ETH', 0.00010), funding_quote('1000PEPE', 0.00010)]
    v = await sim.step(second)
    assert v['rotation']['targets'] == ['LINK', '1000PEPE', 'WLD']
    assert set(sim.pairs) == {'LINK', '1000PEPE', 'WLD'}
    assert sim.closed[-1]['coin'] == 'ETH' and sim.closed[-1]['why'].startswith('ROTATE')
    assert any(m.startswith('CARRY SIM ROTATION SCAN: LINK, 1000PEPE, WLD') for m in v['new_events'])


async def test_carry_basis_stop_ejects_at_minus_three_bps_and_holds_slot_until_next_scan():
    t = [1_000_000.0]
    sim = cl.CarrySim(1000.0, clock=lambda: t[0])
    await sim.step([LINK])
    t[0] += 300.0
    adverse = funding_quote('LINK', 0.00005, bn_mark=20.0, hl_mark=20.017)
    v = await sim.step([adverse])
    assert 'LINK' not in sim.pairs and sim.closed[-1]['why'].startswith('EJECT-BASIS')
    assert 'drag' in sim.closed[-1]['why'] and '<= -3.00 bps' in sim.closed[-1]['why']
    assert v['rotation']['targets'] == [] and v['positions'] == []


async def test_funding_loop_runs_the_simulated_book_only_when_the_profile_asks():
    events = []
    e0 = SimpleNamespace(account={'wallet': 1000.0}, p=eng.PROFILES['demo'], event=events.append)
    r = SimpleNamespace(engines={'SOLUSDT': e0}, carry_state=None, carry_sim_state=None)
    await eng.Runner.funding_loop(r, once=True, fetch=lambda: fx.quotes_from(BN, HL, ('LINK', 'ETH')))
    st = r.carry_sim_state
    assert st['mode'].startswith('SIMULATED') and [x['coin'] for x in st['positions']] == ['LINK']
    assert st['start'] == 1000.0 and any(m.startswith('CARRY SIM OPEN LINK') for m in events)
    off = SimpleNamespace(engines={'SOLUSDT': SimpleNamespace(account={'wallet': 1000.0}, event=events.append,
                                                              p=replace(eng.PROFILES['demo'], carry_sim=False))},
                          carry_state=None, carry_sim_state=None)
    await eng.Runner.funding_loop(off, once=True, fetch=lambda: fx.quotes_from(BN, HL, ('LINK',)))
    assert off.carry_sim_state is None and off.carry_state['held'] == ['LINK']   # the shadow view still runs


# ============================================================================ cockpit (static)
class _Doc(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__()
        self.ids, self.stack, self.errors, self.inline, self.scripts = [], [], [], [], []

    def _attrs(self, tag, attrs):
        a = dict(attrs)
        if 'id' in a:
            self.ids.append(a['id'])
        if 'style' in a or tag == 'style' or any(k.startswith('on') for k in a):
            self.inline.append(tag)                               # the CSP forbids inline styles and handlers
        if tag == 'script':
            self.scripts.append(a.get('src'))

    def handle_starttag(self, tag, attrs):
        self._attrs(tag, attrs)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self._attrs(tag, attrs)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, self.getpos()))
        else:
            self.stack.pop()


def _doc():
    d = _Doc()
    d.feed((WEB / 'index.html').read_text(encoding='utf-8'))
    d.close()
    return d


def test_cockpit_html_is_well_formed_csp_clean_and_has_every_scripted_id():
    d = _doc()
    assert d.errors == [] and d.stack == [] and d.inline == []
    assert len(d.ids) == len(set(d.ids))
    assert d.scripts == ['/terminal.js', '/desk.js', '/app.js']
    used = re.compile(r"(?:\$|txt|cls|fill)\('([A-Za-z][\w-]*[\w])'")
    for name in ('app.js', 'terminal.js', 'desk.js'):
        missing = set(used.findall((WEB / name).read_text(encoding='utf-8'))) - set(d.ids)
        assert not missing, f'{name} references ids missing from index.html: {sorted(missing)}'
    for k in ('sym', 'qty', 'entry', 'mark', 'upnl', 'roe', 'tp', 'sl', 'liq', 'margin'):
        assert f'ck-pos-{k}' in d.ids
    for k in ('FLOW', 'VWAP', 'LIQ', 'DEC'):
        assert f'trig-{k}' in d.ids
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    for agent in ('VESKA', 'RUNE'):
        assert agent in html
    for state in ('CALM_TREND', 'SIDEWAYS', 'JUMPY', 'CRASH'):
        assert f'data-state="{state}"' in html


def test_cockpit_css_locks_one_100vh_screen_without_page_scroll():
    css = (WEB / 'style.css').read_text(encoding='utf-8')
    flat = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    assert flat.count('{') == flat.count('}')
    rule = lambda sel: re.search(re.escape(sel) + r'\{([^}]*)\}', flat).group(1)
    assert 'overflow:hidden' in rule('html,body') and 'height:100vh' in rule('.cockpit')
    assert 'overflow:hidden' in rule('.cockpit') and '24fr 46fr 30fr' in rule('.grid')
    for token in ('#070a0e', 'rgba(16,22,34,.75)', '#00f2fe', '#00ffa3', '#ff3b69', '#ffb703', 'blur(12px)'):
        assert token in flat
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    assert 'NET APR' in html
    assert 'SWARM_OVERRIDE' in (WEB / 'desk.js').read_text(encoding='utf-8')
    assert 'rotation.next_scan' in (WEB / 'app.js').read_text(encoding='utf-8')


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
@pytest.mark.parametrize('name', ['app.js', 'terminal.js', 'desk.js'])
def test_cockpit_scripts_parse(name):
    r = subprocess.run(['node', '--check', str(WEB / name)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
