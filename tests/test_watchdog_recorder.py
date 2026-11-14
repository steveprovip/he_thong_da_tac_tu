"""tools/watchdog_recorder.py: atomic heartbeat, restart on every exit that was not asked for (with back-off), stall
and low-disk handling, clean stop through the stop file, one supervisor per folder, status / ensure, detached launch.
Children are tiny python one-liners: no network, no recorder."""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import watchdog_recorder as wd  # noqa: E402

PY = sys.executable
SLEEP = [PY, '-c', 'import time; time.sleep(120)']
EXIT3 = [PY, '-c', 'import sys; sys.exit(3)']
POLITE_SRC = ('import sys, time, pathlib\n'
              'stop, marker = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])\n'
              'while not stop.exists():\n    time.sleep(0.02)\n'
              'marker.write_text("closed")\n')
FAST = dict(interval=0.1, stall_s=0.0, backoff=(0.05, 0.2), tick=0.02, grace_s=5.0)


def polite(out: Path, marker: str = 'clean.marker') -> list:
    """A child that behaves like the recorder: it ends by itself once the stop file exists."""
    return [PY, '-c', POLITE_SRC, str(out / wd.CHILD_STOP), str(out / marker)]


def wait_for(pred, timeout=20.0, step=0.02):
    end = time.time() + timeout
    while time.time() < end:
        got = pred()
        if got:
            return got
        time.sleep(step)
    raise AssertionError('condition not met in time')


class Running:
    """A Supervisor on a thread; always stopped and joined on exit."""

    def __init__(self, sup: wd.Supervisor):
        self.sup, self.code = sup, []
        self.t = threading.Thread(target=lambda: self.code.append(sup.run()), daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def stop(self):
        (self.sup.out / wd.STOP).write_text('stop', encoding='utf-8')
        self.t.join(30)
        assert not self.t.is_alive()

    def __exit__(self, *exc):
        if self.t.is_alive():
            self.sup.stop_flag = True
            self.t.join(30)

    def hb(self, pred=lambda h: True):
        return wait_for(lambda: (lambda h: h if h and pred(h) else None)(wd.read_heartbeat(self.sup.out)))


def fake_heartbeat(out: Path, **over) -> dict:
    hb = {'ts': time.time(), 'utc': 'x', 'state': 'RUNNING', 'interval_s': 10.0, 'watchdog_pid': os.getpid(),
          'watchdog_create_time': wd.proc_token(os.getpid()), 'watchdog_started_utc': 'x', 'launch': 'test',
          'job': {'in_job': False}, 'recorder_pid': os.getpid(), 'recorder_create_time': wd.proc_token(os.getpid()),
          'recorder_started_utc': 'x', 'recorder_uptime_s': 1.0, 'starts': 1, 'restarts': 0, 'last_exit_code': None,
          'last_exit_reason': None, 'last_exit_utc': None, 'next_start_in_s': None, 'last_data_utc': 'x',
          'stall_s': 300.0, 'data_age_s': 1.0, 'free_gb': 10.0, 'min_free_gb': 8.0, 'deadline_utc': None, 'cmd': []}
    hb.update(over)
    out.mkdir(parents=True, exist_ok=True)
    wd.write_atomic(out / wd.HEARTBEAT, json.dumps(hb))
    return hb


def finished_pid() -> int:
    p = subprocess.Popen([PY, '-c', 'pass'])
    p.wait()
    return p.pid


# ============================================================================= files and processes
def test_the_heartbeat_is_replaced_atomically_and_never_read_half_written(tmp_path):
    payload = {'pad': 'x' * 60_000}
    done = threading.Event()

    def writer():
        for n in range(150):
            wd.write_atomic(tmp_path / wd.HEARTBEAT, json.dumps({**payload, 'n': n}))
        done.set()

    threading.Thread(target=writer, daemon=True).start()
    reads = 0
    while not done.is_set():
        hb = wd.read_heartbeat(tmp_path)
        assert hb is None or len(hb['pad']) == 60_000        # never a truncated or empty file
        reads += hb is not None
    assert reads > 5 and wd.read_heartbeat(tmp_path)['n'] == 149
    assert not list(tmp_path.glob('*.tmp'))


def test_a_process_is_identified_by_pid_and_creation_time():
    me = os.getpid()
    token = wd.proc_token(me)
    assert wd.pid_alive(me) and token is not None and wd.proc_token(me) == token
    assert wd.same_process(me, token) and not wd.same_process(me, token + 5.0)       # a recycled pid is someone else
    gone = finished_pid()
    assert not wd.pid_alive(gone) and not wd.same_process(gone, None) and not wd.pid_alive(None) and not wd.pid_alive(0)
    assert set(wd.job_info()) >= {'in_job'}


def test_the_recorder_command_follows_the_mode_and_carries_the_stop_file(tmp_path):
    light = wd.recorder_cmd('light', None, tmp_path, 8.0, None)
    assert light[1:3] == ['-u', str(ROOT / 'tools' / 'record_market.py')] and '--light' in light and '--liq' not in light
    assert light[light.index('--symbols') + 1] == 'DOGEUSDT,XRPUSDT,1000PEPEUSDT'
    assert light[light.index('--stop-file') + 1] == str(tmp_path / wd.CHILD_STOP)
    assert light[light.index('--min-free-gb') + 1] == '8' and '--duration' not in light
    assert 'pythonw' not in Path(light[0]).name.lower()
    liq = wd.recorder_cmd('liq', None, tmp_path, 8.0, 3600.4)
    assert '--liq' in liq and '--light' not in liq and liq[liq.index('--duration') + 1] == '3600'
    assert liq[liq.index('--symbols') + 1].split(',')[:3] == ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    full = wd.recorder_cmd('full', 'SOLUSDT', tmp_path, 0.0, None)
    assert '--light' not in full and '--liq' not in full and full[full.index('--symbols') + 1] == 'SOLUSDT'
    import record_market as rm
    ap_help = subprocess.run([PY, str(ROOT / 'tools' / 'record_market.py'), '--help'], capture_output=True, text=True)
    assert '--stop-file' in ap_help.stdout and 'stop_file' in rm.run.__code__.co_varnames


def test_the_data_signature_follows_the_files_being_written(tmp_path):
    now = time.time()
    hk = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d-%H')
    (tmp_path / 'DOGEUSDT').mkdir()
    assert wd.data_signature(tmp_path, now) == (0, 0.0)
    cur = tmp_path / 'DOGEUSDT' / f'{hk}.jsonl.gz'
    cur.write_bytes(b'x' * 10)
    (tmp_path / 'DOGEUSDT' / '20200101-00.jsonl.gz').write_bytes(b'y' * 999)        # an old hour: not being written
    s1 = wd.data_signature(tmp_path, now)
    assert s1[0] == 10 and s1[1] > 0
    with open(cur, 'ab') as f:
        f.write(b'z' * 5)
    liq = tmp_path / f'liquidations_{hk[:8]}.jsonl'
    liq.write_text('{}\n')
    s2 = wd.data_signature(tmp_path, now)
    assert s2 != s1 and s2[0] == 15 + liq.stat().st_size


# ============================================================================= supervision
def test_an_exit_nobody_asked_for_is_restarted_with_a_doubling_backoff(tmp_path):
    with Running(wd.Supervisor(lambda rem: EXIT3, tmp_path, **FAST)) as r:
        r.hb(lambda h: h['starts'] >= 5)
        r.stop()
    hb = wd.read_heartbeat(tmp_path)
    assert r.code == [0] and hb['state'] == 'STOPPED' and hb['restarts'] >= 4
    assert (hb['last_exit_code'], hb['last_exit_reason']) == (3, 'exit')
    log = (tmp_path / 'watchdog.log').read_text(encoding='utf-8')
    delays = [float(x.split('restart in ')[1].split(' s')[0]) for x in log.splitlines() if 'restart in ' in x]
    assert delays[:4] == [0.05, 0.1, 0.2, 0.2]                # 5 s, 10 s, ... 300 s at the defaults
    assert wd.status(tmp_path)['exit'] == 2


def test_exit_code_zero_is_not_an_excuse(tmp_path):
    """A recorder that got Ctrl+C returns 0: only a stop the supervisor asked for is expected."""
    with Running(wd.Supervisor(lambda rem: [PY, '-c', 'pass'], tmp_path, **FAST)) as r:
        hb = r.hb(lambda h: h['starts'] >= 3)
        assert hb['last_exit_code'] == 0 and hb['last_exit_reason'] == 'exit'
        r.stop()


def test_a_healthy_recorder_beats_every_interval_and_stops_cleanly_through_the_stop_file(tmp_path):
    with Running(wd.Supervisor(lambda rem: polite(tmp_path), tmp_path, **FAST)) as r:
        hb1 = r.hb(lambda h: h['state'] == 'RUNNING')
        pid = hb1['recorder_pid']
        assert wd.pid_alive(pid) and (tmp_path / 'recorder.pid').read_text().strip() == str(pid)
        assert (tmp_path / 'watchdog.pid').read_text().strip() == str(os.getpid())
        hb2 = r.hb(lambda h: h['ts'] >= hb1['ts'] + 0.3)
        assert hb2['recorder_pid'] == pid and hb2['restarts'] == 0 and hb2['recorder_uptime_s'] > hb1['recorder_uptime_s']
        st = wd.status(tmp_path)
        assert (st['health'], st['exit'], st['watchdog_alive'], st['recorder_alive']) == ('OK', 0, True, True)
        assert 'recorder watchdog: OK' in wd.render(st)
        r.stop()
    assert (tmp_path / 'clean.marker').read_text() == 'closed'          # the child ended by itself: files closed
    log = (tmp_path / 'watchdog.log').read_text(encoding='utf-8')
    assert 'clean, exit 0' in log and 'terminated' not in log
    hb = wd.read_heartbeat(tmp_path)
    assert hb['state'] == 'STOPPED' and hb['recorder_pid'] is None and not wd.pid_alive(pid)
    assert not any((tmp_path / n).exists() for n in ('recorder.pid', 'watchdog.pid', wd.STOP, wd.CHILD_STOP))
    assert wd.status(tmp_path)['health'] == 'STOPPED'


def test_a_recorder_that_ignores_the_stop_file_is_terminated_after_the_grace_period(tmp_path):
    with Running(wd.Supervisor(lambda rem: SLEEP, tmp_path, **{**FAST, 'grace_s': 0.3})) as r:
        pid = r.hb(lambda h: h['state'] == 'RUNNING')['recorder_pid']
        r.stop()
    assert not wd.pid_alive(pid) and 'terminated' in (tmp_path / 'watchdog.log').read_text(encoding='utf-8')


def test_a_recorder_that_is_alive_but_writes_nothing_is_restarted(tmp_path):
    quiet, busy = tmp_path / 'quiet', tmp_path / 'busy'
    opts = {**FAST, 'stall_s': 0.4, 'grace_s': 0.2}
    with Running(wd.Supervisor(lambda rem: SLEEP, quiet, watch=lambda: 0, **opts)) as q, \
            Running(wd.Supervisor(lambda rem: SLEEP, busy, watch=time.time, **opts)) as b:
        hb = q.hb(lambda h: h['restarts'] >= 1)
        assert hb['last_exit_reason'] == 'stall'
        assert b.hb(lambda h: h['state'] == 'RUNNING')['restarts'] == 0     # same age, data flowing: left alone
        assert 'no data for' in (quiet / 'watchdog.log').read_text(encoding='utf-8')
        q.stop(), b.stop()
    assert wd.read_heartbeat(busy)['restarts'] == 0


def test_the_planned_duration_ends_in_done_and_a_restart_gets_only_the_time_left(tmp_path):
    left = []

    def cmd_for(rem):
        left.append(rem)
        return EXIT3

    sup = wd.Supervisor(cmd_for, tmp_path, deadline=time.time() + 2.5, **FAST)
    with Running(sup) as r:
        r.t.join(30)
    hb = wd.read_heartbeat(tmp_path)
    assert r.code == [0] and hb['state'] == 'DONE' and hb['deadline_utc'] is not None
    assert len(left) >= 3 and left == sorted(left, reverse=True) and 1.0 <= left[-1] < left[0] <= 2.5   # floor 1 s
    assert wd.status(tmp_path)['exit'] == 2


def test_low_disk_waits_instead_of_starting(tmp_path, monkeypatch):
    free = [1.0]
    monkeypatch.setattr(wd, 'free_gb', lambda p: free[0])
    with Running(wd.Supervisor(lambda rem: SLEEP, tmp_path, min_free_gb=8.0, **{**FAST, 'grace_s': 0.2})) as r:
        hb = r.hb(lambda h: h['state'] == 'DISK_LOW')
        assert hb['starts'] == 0 and hb['recorder_pid'] is None and wd.status(tmp_path)['exit'] == 1
        free[0] = 100.0
        assert r.hb(lambda h: h['state'] == 'RUNNING')['starts'] == 1
        r.stop()


def test_giving_up_after_max_restarts_is_a_final_state(tmp_path):
    with Running(wd.Supervisor(lambda rem: EXIT3, tmp_path, max_restarts=2, **FAST)) as r:
        r.t.join(30)
    hb = wd.read_heartbeat(tmp_path)
    assert hb['state'] == 'FAILED' and hb['starts'] == 3 and wd.status(tmp_path)['exit'] == 1


def test_only_one_supervisor_per_output_folder(tmp_path):
    with Running(wd.Supervisor(lambda rem: polite(tmp_path), tmp_path, **FAST)) as r:
        pid = r.hb(lambda h: h['state'] == 'RUNNING')['recorder_pid']
        assert wd.Supervisor(lambda rem: SLEEP, tmp_path, **FAST).run() == 4
        assert wd.read_heartbeat(tmp_path)['recorder_pid'] == pid and wd.pid_alive(pid)
        r.stop()
    assert 'another supervisor' in (tmp_path / 'watchdog.log').read_text(encoding='utf-8')


def test_an_orphan_recorder_of_a_dead_supervisor_is_stopped_before_a_new_one_starts(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    orphan = subprocess.Popen(polite(tmp_path, 'orphan.marker'))
    try:
        fake_heartbeat(tmp_path, watchdog_pid=finished_pid(), watchdog_create_time=1.0, recorder_pid=orphan.pid,
                       recorder_create_time=wd.proc_token(orphan.pid))
        assert wd.status(tmp_path)['health'] == 'DEAD' and wd.status(tmp_path)['recorder_alive']
        with Running(wd.Supervisor(lambda rem: polite(tmp_path), tmp_path, **FAST)) as r:
            hb = r.hb(lambda h: h['state'] == 'RUNNING' and h['recorder_pid'] != orphan.pid)
            assert orphan.wait(10) == 0 and (tmp_path / 'orphan.marker').exists()
            assert wd.pid_alive(hb['recorder_pid']) and not (tmp_path / 'clean.marker').exists()
            r.stop()
        assert 'orphan recorder' in (tmp_path / 'watchdog.log').read_text(encoding='utf-8')
    finally:
        if orphan.poll() is None:
            orphan.kill()


# ============================================================================= status / ensure
def test_status_tells_never_started_dead_stale_and_stopped_apart(tmp_path):
    st = wd.status(tmp_path / 'none')
    assert (st['health'], st['exit']) == ('NEVER_STARTED', 3) and 'NEVER STARTED' in wd.render(st)
    fake_heartbeat(tmp_path)
    assert (wd.status(tmp_path)['health'], wd.status(tmp_path)['exit']) == ('OK', 0)
    fake_heartbeat(tmp_path, ts=time.time() - 31.0)                      # 3 intervals without a beat
    st = wd.status(tmp_path)
    assert (st['health'], st['exit'], st['heartbeat_fresh']) == ('DEAD', 1, False) and 'STALE' in wd.render(st)
    fake_heartbeat(tmp_path, watchdog_pid=finished_pid(), watchdog_create_time=1.0)
    assert wd.status(tmp_path)['health'] == 'DEAD' and wd.status(tmp_path)['recorder_alive']
    fake_heartbeat(tmp_path, recorder_pid=finished_pid(), recorder_create_time=1.0)
    assert wd.status(tmp_path)['health'] == 'DEAD'                       # RUNNING on paper, no recorder process
    fake_heartbeat(tmp_path, state='BACKOFF', recorder_pid=None, next_start_in_s=3.0)
    st = wd.status(tmp_path)
    assert (st['health'], st['exit']) == ('BACKOFF', 1) and 'next start in 3.0 s' in wd.render(st)
    for state in ('STOPPED', 'DONE'):
        fake_heartbeat(tmp_path, state=state, ts=time.time() - 9999, recorder_pid=None)
        assert (wd.status(tmp_path)['health'], wd.status(tmp_path)['exit']) == (state, 2)
    fake_heartbeat(tmp_path, job={'in_job': True, 'kill_on_close': True})
    assert 'NOT decoupled' in wd.render(wd.status(tmp_path))


def test_the_status_command_prints_json_and_returns_the_health_code(tmp_path, capsys):
    assert wd.main(['status', '--out', str(tmp_path)]) == 3
    fake_heartbeat(tmp_path)
    capsys.readouterr()
    assert wd.main(['status', '--out', str(tmp_path), '--json']) == 0
    got = json.loads(capsys.readouterr().out)
    assert got['health'] == 'OK' and got['heartbeat']['state'] == 'RUNNING' and got['watchdog_alive'] is True


def test_ensure_restarts_a_dead_supervisor_and_never_undoes_a_stop(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(wd, 'start', lambda args, out, method='auto', wait_s=30.0: calls.append((args, method)) or
                        {**wd.status(out), 'started': True, 'spawned_pid': 1, 'method': method})
    assert wd.main(['ensure', '--out', str(tmp_path)]) == 3 and not calls          # never started, nothing saved
    wd.write_atomic(tmp_path / wd.CONFIG, json.dumps({'run_args': ['--mode', 'light'], 'launch': 'auto'}))
    fake_heartbeat(tmp_path)
    assert wd.main(['ensure', '--out', str(tmp_path)]) == 0 and not calls          # alive: left alone
    fake_heartbeat(tmp_path, state='STOPPED', recorder_pid=None)
    assert wd.main(['ensure', '--out', str(tmp_path)]) == 2 and not calls          # stopped on purpose: stays stopped
    fake_heartbeat(tmp_path, ts=time.time() - 600.0)
    wd.main(['ensure', '--out', str(tmp_path)])
    assert calls == [(['--mode', 'light'], 'auto')]                                # dead: started with the saved options
    assert 'restarted' in capsys.readouterr().out


def test_start_refuses_a_second_supervisor_and_saves_its_options(tmp_path, monkeypatch, capsys):
    spawned = []
    monkeypatch.setattr(wd, 'spawn_detached', lambda args, method='auto': spawned.append(args) or (4242, 'test'))
    fake_heartbeat(tmp_path)
    assert wd.main(['start', '--out', str(tmp_path), '--mode', 'liq', '--days', '7']) == 0 and not spawned
    assert 'already at work' in capsys.readouterr().out
    cfg = json.loads((tmp_path / wd.CONFIG).read_text(encoding='utf-8'))
    args = cfg['run_args']
    assert args[args.index('--mode') + 1] == 'liq' and '--deadline' in args
    assert abs(float(args[args.index('--deadline') + 1]) - (time.time() + 7 * 86400)) < 60


# ============================================================================= detached launch (real processes)
def test_start_launches_a_supervisor_that_outlives_its_launcher_and_stop_ends_it(tmp_path):
    out = tmp_path / 'rec'
    cli = [PY, str(ROOT / 'tools' / 'watchdog_recorder.py')]
    pids = []
    try:
        r = subprocess.run(cli + ['start', '--out', str(out), '--child-cmd', json.dumps(SLEEP), '--interval', '0.5',
                                  '--stall-s', '0', '--grace-s', '1', '--min-free-gb', '0'],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
        hb1 = wait_for(lambda: (lambda h: h if h and h['state'] == 'RUNNING' else None)(wd.read_heartbeat(out)))
        pids = [hb1['watchdog_pid'], hb1['recorder_pid']]
        assert hb1['watchdog_pid'] != os.getpid() and all(wd.pid_alive(p) for p in pids)
        assert hb1['launch'] in ('breakaway', 'wmi', 'attached', 'setsid')
        if hb1['launch'] in ('breakaway', 'wmi'):
            assert hb1['job'].get('kill_on_close') is not True          # out of the launcher's job
        hb2 = wait_for(lambda: (lambda h: h if h['ts'] >= hb1['ts'] + 1.0 else None)(wd.read_heartbeat(out)))
        assert hb2['watchdog_pid'] == hb1['watchdog_pid'] and hb2['restarts'] == 0     # the launcher is long gone
        s = subprocess.run(cli + ['status', '--out', str(out), '--json'], capture_output=True, text=True, timeout=60)
        assert s.returncode == 0 and json.loads(s.stdout)['health'] == 'OK'
        s = subprocess.run(cli + ['stop', '--out', str(out)], capture_output=True, text=True, timeout=120)
        assert s.returncode == 2 and 'STOPPED' in s.stdout, s.stdout + s.stderr
        wait_for(lambda: not any(wd.pid_alive(p) for p in pids))
    finally:
        for p in pids:
            if wd.pid_alive(p):
                try:
                    os.kill(p, 15)
                except OSError:
                    pass
