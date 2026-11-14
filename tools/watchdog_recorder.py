#!/usr/bin/env python3
"""Detached supervisor for tools/record_market.py: restart on any exit it did not ask for, atomic heartbeat, status.

    python tools/watchdog_recorder.py start  [--mode light|liq|full] [--symbols A,B] [--days 7] [--min-free-gb 8]
    python tools/watchdog_recorder.py status [--json]     exit 0 recording | 1 unhealthy | 2 stopped | 3 never started
    python tools/watchdog_recorder.py stop                clean stop: the recorder closes its gzip members
    python tools/watchdog_recorder.py ensure              start again with the saved options unless one is alive
    python tools/watchdog_recorder.py run ...             the supervisor itself, in the foreground (`start` spawns it)

Why: the 2026-11-10 recorder was a child of an agent session and ended with it after 10.4 h, without a log line.
`start` therefore launches the supervisor with no console (pythonw, DETACHED_PROCESS, its own process group: no
Ctrl+C can reach it) and outside the caller's job object (CREATE_BREAKAWAY_FROM_JOB; if the job refuses, through
WMI Win32_Process.Create, whose child belongs to the WMI host). The supervisor reports the job it really sits in
(`job` in the heartbeat): `kill_on_close: true` means the launch did not get out.  For a restart after a reboot or
after the supervisor itself is killed, schedule `ensure` (owner's call, it is persistent system configuration):
    schtasks /Create /TN RecorderWatchdog /SC MINUTE /MO 5 /F /TR "\"<pythonw.exe>\" \"<this file>\" ensure"
`ensure` never undoes a `stop`.

Files in --out (default data/recordings):
    recorder.heartbeat   JSON, replaced atomically every --interval seconds (10): state, pids, restarts, last exit,
                         age of the last data write, free disk, job facts
    watchdog.pid / recorder.pid, watchdog.log (events), recorder.log / recorder.err (the recorder's own output),
    watchdog.json (options of the last start), watchdog.stop / recorder.stop (stop requests), watchdog.lock
States: RUNNING, BACKOFF (waiting to restart: 5 s doubling to 300 s, reset after 10 min up), DISK_LOW (below
--min-free-gb: waits, never fills the disk), STOPPING, STOPPED (asked), DONE (--days elapsed), FAILED (--max-restarts).
A recorder that is alive but has written nothing for --stall-s (300) is stopped and started again.
Public market data only: no key, no order, no engine.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = os.name == 'nt'
HEARTBEAT, STOP, CHILD_STOP, CONFIG = 'recorder.heartbeat', 'watchdog.stop', 'recorder.stop', 'watchdog.json'
MODE_SYMBOLS = {'light': 'DOGEUSDT,XRPUSDT,1000PEPEUSDT', 'full': 'DOGEUSDT,XRPUSDT,1000PEPEUSDT',
                'liq': 'BTCUSDT,ETHUSDT,SOLUSDT,DOGEUSDT,XRPUSDT,1000PEPEUSDT'}
ALIVE = ('OK', 'BACKOFF', 'DISK_LOW', 'STOPPING')          # a supervisor is at work: do not start another
FINAL = ('STOPPED', 'DONE', 'FAILED')
EXIT = {'OK': 0, 'STOPPED': 2, 'DONE': 2, 'NEVER_STARTED': 3}   # anything else: 1
DETACHED_PROCESS, NEW_GROUP, NO_WINDOW, BREAKAWAY = 0x8, 0x200, 0x08000000, 0x01000000

if WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    _k32.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))
    _k32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD))

    class _BasicLimit(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class _ExtLimit(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _BasicLimit), ('IoInfo', ctypes.c_uint64 * 6),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    def _open(pid: int):
        return _k32.OpenProcess(0x1000 | 0x100000, False, int(pid))    # QUERY_LIMITED_INFORMATION | SYNCHRONIZE


# ------------------------------------------------------------------------------------------------- processes
def pid_alive(pid) -> bool:
    if not pid or int(pid) <= 0:
        return False
    if WIN:
        h = _open(pid)
        if not h:
            return ctypes.get_last_error() == 5              # access denied: it exists and is not ours
        try:
            return _k32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            _k32.CloseHandle(h)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def proc_token(pid):
    """A value that identifies this process and not a later one that got the same pid (creation time); None if the
    process cannot be read."""
    if not pid:
        return None
    if WIN:
        h = _open(pid)
        if not h:
            return None
        try:
            t = [wintypes.FILETIME() for _ in range(4)]
            if not _k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
                return None
            return round(((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime) / 1e7 - 11644473600.0, 3)
        finally:
            _k32.CloseHandle(h)
    try:
        return float(Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def same_process(pid, token) -> bool:
    return pid_alive(pid) and (token is None or proc_token(pid) == token)


def job_info() -> dict:
    """Is this process inside a Windows job object, and does that job kill its members when it closes."""
    if not WIN:
        return {'in_job': None}
    flag = wintypes.BOOL()
    if not _k32.IsProcessInJob(_k32.GetCurrentProcess(), None, ctypes.byref(flag)):
        return {'in_job': None}
    out = {'in_job': bool(flag.value)}
    if flag.value:
        info = _ExtLimit()
        if _k32.QueryInformationJobObject(None, 9, ctypes.byref(info), ctypes.sizeof(info), None):
            f = info.BasicLimitInformation.LimitFlags
            out.update(kill_on_close=bool(f & 0x2000), breakaway_ok=bool(f & 0x800), silent_breakaway=bool(f & 0x1000))
    return out


def console_python(windowless: bool = False) -> str:
    """python.exe for a child whose output is captured; pythonw.exe for the supervisor (no console at all)."""
    exe = Path(sys.executable)
    if WIN:
        want = exe.with_name('pythonw.exe' if windowless else 'python.exe')
        if want.exists():
            return str(want)
    return str(exe)


def acquire_lock(path: Path):
    """An OS lock held for the life of the process (released by the OS when it dies). None if someone holds it."""
    f = open(path, 'a+b')
    try:
        if WIN:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# ------------------------------------------------------------------------------------------------- files
def iso(t) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def write_atomic(path: Path, text: str) -> bool:
    """Write-then-rename: a reader sees the old file or the new one, never a partial one."""
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:                              # Windows: a reader has the target open right now
            time.sleep(0.02)
    tmp.unlink(missing_ok=True)
    return False


def read_heartbeat(out: Path):
    for _ in range(20):
        try:
            return json.loads((Path(out) / HEARTBEAT).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (PermissionError, ValueError):                # mid-rename on Windows: try again
            time.sleep(0.02)
    return None


def data_signature(out: Path, now: float | None = None) -> tuple:
    """(bytes, newest mtime) of the files the recorder is writing now: this and the previous UTC hour of every
    symbol folder and today's liquidation file. It changes whenever a line reaches the disk."""
    now = time.time() if now is None else now
    keys = {datetime.fromtimestamp(now - d, timezone.utc).strftime('%Y%m%d-%H') for d in (0, 3600)}
    days = {k[:8] for k in keys}
    files = [p for k in keys for p in Path(out).glob(f'*/{k}.jsonl.gz')]
    files += [p for d in days for p in Path(out).glob(f'liquidations_{d}.jsonl')]
    size, newest = 0, 0.0
    for p in files:
        try:
            st = p.stat()
        except OSError:
            continue
        size += st.st_size
        newest = max(newest, st.st_mtime)
    return size, newest


def recorder_cmd(mode: str, symbols: str | None, out: Path, min_free_gb: float, remaining_s: float | None) -> list:
    cmd = [console_python(), '-u', str(ROOT / 'tools' / 'record_market.py'), '--symbols', symbols or MODE_SYMBOLS[mode],
           '--out', str(out), '--stop-file', str(Path(out) / CHILD_STOP), '--min-free-gb', f'{min_free_gb:g}']
    cmd += {'light': ['--light'], 'liq': ['--liq'], 'full': []}[mode]
    if remaining_s:
        cmd += ['--duration', f'{max(1.0, remaining_s):.0f}']
    return cmd


# ------------------------------------------------------------------------------------------------- supervisor
class Supervisor:
    """One recorder child, restarted on every exit that was not asked for. `cmd_for(remaining seconds | None)` gives
    the child's command line; `watch()` a value that changes when the child writes data."""

    def __init__(self, cmd_for, out: Path, interval: float = 10.0, stall_s: float = 300.0, min_free_gb: float = 0.0,
                 deadline: float | None = None, backoff: tuple = (5.0, 300.0), healthy_s: float = 600.0,
                 grace_s: float = 20.0, tick: float = 1.0, max_restarts: int = 0, launch: str = 'foreground',
                 watch=None):
        self.cmd_for, self.out = cmd_for, Path(out)
        self.interval, self.stall_s, self.min_free_gb, self.deadline = interval, stall_s, min_free_gb, deadline
        self.backoff, self.healthy_s, self.grace_s, self.tick = backoff, healthy_s, grace_s, tick
        self.max_restarts, self.launch = max_restarts, launch
        self.watch = watch if watch is not None else (lambda: data_signature(self.out))
        self.state, self.child, self.cmd = 'STARTING', None, None
        self.starts = self.fails = 0
        self.child_started = self.child_token = None
        self.next_start = 0.0
        self.last_exit = {'code': None, 'reason': None, 'utc': None}
        self.sig, self.last_change, self.last_beat = None, None, 0.0
        self.stop_flag = False
        self.t0, self.token, self.job = time.time(), proc_token(os.getpid()), job_info()

    # -- small helpers
    def log(self, msg: str):
        try:
            with open(self.out / 'watchdog.log', 'a', encoding='utf-8') as f:
                f.write(f'{iso(time.time())} {msg}\n')
        except OSError:
            pass

    def snapshot(self, now: float) -> dict:
        pid = self.child.pid if self.child is not None else None
        return {'ts': now, 'utc': iso(now), 'state': self.state, 'interval_s': self.interval,
                'watchdog_pid': os.getpid(), 'watchdog_create_time': self.token, 'watchdog_started_utc': iso(self.t0),
                'launch': self.launch, 'job': self.job,
                'recorder_pid': pid, 'recorder_create_time': self.child_token if pid else None,
                'recorder_started_utc': iso(self.child_started) if pid else None,
                'recorder_uptime_s': round(now - self.child_started, 1) if pid else None,
                'starts': self.starts, 'restarts': max(0, self.starts - 1),
                'last_exit_code': self.last_exit['code'], 'last_exit_reason': self.last_exit['reason'],
                'last_exit_utc': self.last_exit['utc'],
                'next_start_in_s': round(max(0.0, self.next_start - now), 1) if self.state == 'BACKOFF' else None,
                'last_data_utc': iso(self.last_change), 'stall_s': self.stall_s,
                'data_age_s': round(now - self.last_change, 1) if self.last_change is not None else None,
                'free_gb': round(free_gb(self.out), 2), 'min_free_gb': self.min_free_gb,
                'deadline_utc': iso(self.deadline), 'cmd': self.cmd}

    def beat(self, now: float):
        self.last_beat = now
        write_atomic(self.out / HEARTBEAT, json.dumps(self.snapshot(now), indent=1) + '\n')

    def set_state(self, state: str, now: float):
        if state != self.state:
            self.state = state
            self.beat(now)

    # -- the child
    def spawn(self, now: float):
        remaining = None if self.deadline is None else max(1.0, self.deadline - now)
        self.cmd = [str(x) for x in self.cmd_for(remaining)]
        (self.out / CHILD_STOP).unlink(missing_ok=True)
        with open(self.out / 'recorder.log', 'ab') as so, open(self.out / 'recorder.err', 'ab') as se:
            kw = {'creationflags': NO_WINDOW | NEW_GROUP} if WIN else {'start_new_session': True}
            self.child = subprocess.Popen(self.cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=so, stderr=se,
                                          close_fds=True, **kw)
        self.starts += 1
        self.child_started, self.child_token = now, proc_token(self.child.pid)
        self.sig, self.last_change = self.watch(), now
        write_atomic(self.out / 'recorder.pid', f'{self.child.pid}\n')
        self.log(f'recorder started: pid {self.child.pid}, start {self.starts}: {subprocess.list2cmdline(self.cmd)}')

    def stop_child(self, why: str):
        """Ask through the stop file (the recorder closes its files), then terminate, then kill."""
        if self.child is None:
            return
        (self.out / CHILD_STOP).write_text(why + '\n', encoding='utf-8')
        try:
            self.child.wait(timeout=self.grace_s)
            how = 'clean'
        except subprocess.TimeoutExpired:
            self.child.terminate()
            try:
                self.child.wait(timeout=5.0)
                how = 'terminated'
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
                how = 'killed'
        self.log(f'recorder stopped ({why}): pid {self.child.pid}, {how}, exit {self.child.returncode}')
        self.child = None
        for name in (CHILD_STOP, 'recorder.pid'):
            (self.out / name).unlink(missing_ok=True)

    def exited(self, code, reason: str, now: float):
        self.fails += 1
        delay = min(self.backoff[1], self.backoff[0] * 2 ** (self.fails - 1))
        self.next_start = now + delay
        self.last_exit = {'code': code, 'reason': reason, 'utc': iso(now)}
        self.child = None
        (self.out / 'recorder.pid').unlink(missing_ok=True)
        self.log(f'recorder gone ({reason}, exit {code}) after {now - self.child_started:.0f} s: restart in {delay:g} s')

    def adopt_orphan(self):
        """A recorder left by a supervisor that was killed: stop it before starting ours (two writers would
        interleave the same hourly files)."""
        prev = read_heartbeat(self.out) or {}
        pid, token = prev.get('recorder_pid'), prev.get('recorder_create_time')
        if not pid or token is None or not same_process(pid, token):
            return
        self.log(f'orphan recorder pid {pid} from supervisor {prev.get("watchdog_pid")}: stopping it')
        (self.out / CHILD_STOP).write_text('orphan\n', encoding='utf-8')
        end = time.time() + self.grace_s
        while time.time() < end and same_process(pid, token):
            time.sleep(0.2)
        if same_process(pid, token):
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        (self.out / CHILD_STOP).unlink(missing_ok=True)

    # -- the loop
    def step(self, now: float) -> bool:
        """One pass. False = finished."""
        if self.stop_flag or (self.out / STOP).exists():
            self.set_state('STOPPING', now)
            self.stop_child('stop requested')
            self.state = 'STOPPED'
            return False
        if self.deadline is not None and now >= self.deadline:
            self.set_state('STOPPING', now)
            self.stop_child('planned duration over')
            self.state = 'DONE'
            return False
        if self.child is not None:
            code = self.child.poll()
            if code is not None:
                self.exited(code, 'exit', now)
            else:
                if self.stall_s and now - self.last_beat >= min(self.interval, self.stall_s / 3):
                    sig = self.watch()
                    if sig != self.sig:
                        self.sig, self.last_change = sig, now
                if self.stall_s and now - self.last_change > self.stall_s:
                    self.stop_child(f'no data for {now - self.last_change:.0f} s')
                    self.exited(None, 'stall', now)
                elif now - self.child_started >= self.healthy_s:
                    self.fails = 0
        if self.child is None:
            if self.max_restarts and self.starts > self.max_restarts:
                self.state = 'FAILED'
                return False
            if self.min_free_gb and free_gb(self.out) < self.min_free_gb:
                self.set_state('DISK_LOW', now)
            elif now >= self.next_start:
                try:
                    self.spawn(now)
                    self.set_state('RUNNING', now)
                except OSError as e:
                    self.child, self.child_started = None, now
                    self.exited(None, f'spawn failed: {e}', now)
                    self.set_state('BACKOFF', now)
            else:
                self.set_state('BACKOFF', now)
        if now - self.last_beat >= self.interval:
            self.beat(now)
        return True

    def run(self) -> int:
        self.out.mkdir(parents=True, exist_ok=True)
        lock = acquire_lock(self.out / 'watchdog.lock')
        if lock is None:
            self.log(f'pid {os.getpid()}: another supervisor holds {self.out / "watchdog.lock"}: not starting')
            return 4
        try:
            for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, 'SIGBREAK', None)):
                if sig is not None:
                    try:
                        signal.signal(sig, lambda *_: setattr(self, 'stop_flag', True))
                    except ValueError:                       # not the main thread (tests)
                        pass
            self.adopt_orphan()
            (self.out / STOP).unlink(missing_ok=True)
            write_atomic(self.out / 'watchdog.pid', f'{os.getpid()}\n')
            self.log(f'supervisor started: pid {os.getpid()}, launch {self.launch}, job {json.dumps(self.job)}')
            while True:
                try:
                    if not self.step(time.time()):
                        break
                except Exception:                            # a supervisor that dies of a transient error is no use
                    self.log('step failed:\n' + traceback.format_exc())
                time.sleep(self.tick)
            self.beat(time.time())
            self.log(f'supervisor finished: {self.state}, {self.starts} start(s)')
            for name in (STOP, 'watchdog.pid'):
                (self.out / name).unlink(missing_ok=True)
            return 0
        finally:
            lock.close()


# ------------------------------------------------------------------------------------------------- status
def status(out: Path, now: float | None = None) -> dict:
    """The heartbeat plus what can be checked from outside: is it fresh, do the two processes still exist."""
    now = time.time() if now is None else now
    hb = read_heartbeat(out)
    if hb is None:
        return {'health': 'NEVER_STARTED', 'exit': EXIT['NEVER_STARTED'], 'out': str(out)}
    age = now - hb['ts']
    fresh = age <= max(3.0 * hb.get('interval_s', 10.0), 5.0)
    wd = same_process(hb.get('watchdog_pid'), hb.get('watchdog_create_time'))
    rec = bool(hb.get('recorder_pid')) and same_process(hb.get('recorder_pid'), hb.get('recorder_create_time'))
    if hb['state'] in FINAL:
        health = hb['state']
    elif not fresh or not wd:
        health = 'DEAD'                                       # the supervisor is gone or hung; the recorder may live on
    elif hb['state'] == 'RUNNING':
        health = 'OK' if rec else 'DEAD'
    else:
        health = hb['state']
    return {'health': health, 'exit': EXIT.get(health, 1), 'out': str(out), 'heartbeat_age_s': round(age, 1),
            'heartbeat_fresh': fresh, 'watchdog_alive': wd, 'recorder_alive': rec, 'heartbeat': hb}


def _span(s) -> str:
    if s is None:
        return 'n/a'
    s = int(s)
    return f'{s} s' if s < 120 else (f'{s // 60} min' if s < 7200 else f'{s // 3600} h {s % 3600 // 60:02d} min')


def render(st: dict) -> str:
    if st['health'] == 'NEVER_STARTED':
        return f"recorder watchdog: NEVER STARTED (no {HEARTBEAT} in {st['out']})"
    hb, job = st['heartbeat'], st['heartbeat'].get('job') or {}
    jobtxt = ('no job object' if job.get('in_job') is False else 'job facts unknown' if job.get('in_job') is None else
              'inside a job that kills its members when it closes: NOT decoupled' if job.get('kill_on_close')
              else 'inside a job without kill-on-close')
    exit_txt = ('none' if hb['last_exit_reason'] is None else
                f"{hb['last_exit_reason']}, code {hb['last_exit_code']}, {hb['last_exit_utc']}")
    gone = 'ended' if hb['state'] in FINAL else 'GONE'
    stall = f"restart after {hb['stall_s']:g} s of silence" if hb['stall_s'] else 'stall check off'
    lines = [f"recorder watchdog: {st['health']} (state {hb['state']})",
             f"  heartbeat  {hb['utc']}, {_span(st['heartbeat_age_s'])} ago (every {hb['interval_s']:g} s)"
             + ('' if st['heartbeat_fresh'] else '  STALE'),
             f"  watchdog   pid {hb['watchdog_pid']} {'alive' if st['watchdog_alive'] else gone}, started "
             f"{hb['watchdog_started_utc']}, launch {hb['launch']}, {jobtxt}",
             f"  recorder   pid {hb['recorder_pid']} {'alive' if st['recorder_alive'] else 'not running'}, up "
             f"{_span(hb['recorder_uptime_s'])}, restarts {hb['restarts']}, last exit: {exit_txt}",
             f"  data       last write {_span(hb['data_age_s'])} ago ({stall})",
             f"  disk       {hb['free_gb']:.1f} GB free (waits below {hb['min_free_gb']:g} GB)"
             + (f", planned end {hb['deadline_utc']}" if hb['deadline_utc'] else '')]
    if hb['state'] == 'BACKOFF':
        lines.append(f"  next start in {hb['next_start_in_s']} s")
    return '\n'.join(lines)


# ------------------------------------------------------------------------------------------------- launch
def _wmi_create(cmdline: str, cwd: str) -> int:
    """Win32_Process.Create: the new process is a child of the WMI provider host, not of this session."""
    q = lambda s: s.replace("'", "''")                       # noqa: E731
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
          f"@{{CommandLine = '{q(cmdline)}'; CurrentDirectory = '{q(cwd)}'}}; \"$($r.ReturnValue) $($r.ProcessId)\"")
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], capture_output=True, text=True,
                       timeout=90)
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) < 2 or parts[-2] != '0':
        raise OSError(f'Win32_Process.Create failed: {r.stdout.strip()} {r.stderr.strip()[:200]}')
    return int(parts[-1])


def spawn_detached(run_args: list, method: str = 'auto') -> tuple:
    """Start `watchdog_recorder.py run <run_args>` outside this console, process group and job. -> (pid, method)."""
    base = [console_python(windowless=True), str(Path(__file__).resolve()), 'run', *run_args, '--detached']
    null = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'close_fds': True}
    if not WIN:
        return subprocess.Popen(base + ['--launch', 'setsid'], cwd=str(ROOT), start_new_session=True, **null).pid, 'setsid'
    errors = []
    for m in (('breakaway', 'wmi', 'attached') if method == 'auto' else (method,)):
        cmd = base + ['--launch', m]
        try:
            if m == 'wmi':
                return _wmi_create(subprocess.list2cmdline(cmd), str(ROOT)), m
            flags = DETACHED_PROCESS | NEW_GROUP | (BREAKAWAY if m == 'breakaway' else 0)
            return subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags, **null).pid, m
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(f'{m}: {e}')
    raise OSError('; '.join(errors))


def run_args_of(a, deadline) -> list:
    args = ['--mode', a.mode, '--out', str(a.out), '--min-free-gb', f'{a.min_free_gb:g}', '--interval', f'{a.interval:g}',
            '--stall-s', f'{a.stall_s:g}', '--grace-s', f'{a.grace_s:g}', '--max-restarts', str(a.max_restarts)]
    if a.symbols:
        args += ['--symbols', a.symbols]
    if deadline:
        args += ['--deadline', f'{deadline:.0f}']
    if a.child_cmd:
        args += ['--child-cmd', a.child_cmd]
    return args


def start(run_args: list, out: Path, method: str = 'auto', wait_s: float = 30.0) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    st = status(out)
    if st['health'] in ALIVE:
        return {**st, 'started': False, 'note': 'a supervisor is already at work'}
    (out / STOP).unlink(missing_ok=True)
    t0 = time.time()
    pid, used = spawn_detached(run_args, method)
    while time.time() - t0 < wait_s:
        st = status(out)
        hb = st.get('heartbeat')
        if hb and hb['ts'] >= t0 and (hb['state'] != 'STARTING'):
            break
        time.sleep(0.25)
    return {**st, 'started': True, 'spawned_pid': pid, 'method': used}


# ------------------------------------------------------------------------------------------------- cli
def _options(ap):
    ap.add_argument('--mode', choices=sorted(MODE_SYMBOLS), default='light',
                    help='light: depth@100ms + aggTrade (~0.43 GB/day, 3 coins); liq: all liquidations + touch + prints '
                         '(~2.6 GB/day, 6 coins); full: every stream')
    ap.add_argument('--symbols', default=None, help='default by mode: ' + json.dumps(MODE_SYMBOLS))
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'recordings')
    ap.add_argument('--min-free-gb', type=float, default=8.0, help='wait (state DISK_LOW) below this much free disk')
    ap.add_argument('--days', type=float, default=0.0, help='planned length; 0 = until `stop`')
    ap.add_argument('--deadline', type=float, default=0.0, help=argparse.SUPPRESS)
    ap.add_argument('--interval', type=float, default=10.0, help='heartbeat period, seconds')
    ap.add_argument('--stall-s', type=float, default=300.0, help='restart a recorder that wrote nothing this long; 0 = off')
    ap.add_argument('--grace-s', type=float, default=20.0, help='how long a stop waits before terminating')
    ap.add_argument('--max-restarts', type=int, default=0, help='give up (FAILED) after this many restarts; 0 = never')
    ap.add_argument('--child-cmd', default=None,
                    help='JSON list: supervise this command instead of the recorder (tests, other long jobs)')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='action', required=True)
    p_start = sub.add_parser('start', help='launch the supervisor detached and return')
    _options(p_start)
    p_start.add_argument('--launch', choices=('auto', 'breakaway', 'wmi', 'attached'), default='auto')
    p_run = sub.add_parser('run', help='the supervisor, in the foreground')
    _options(p_run)
    p_run.add_argument('--launch', default='foreground')
    p_run.add_argument('--detached', action='store_true', help=argparse.SUPPRESS)
    for name, text in (('status', 'print the heartbeat and what can be verified from outside'),
                       ('stop', 'ask the supervisor to stop the recorder cleanly and end'),
                       ('ensure', 'start with the saved options unless a supervisor is alive (for a scheduled task)')):
        p = sub.add_parser(name, help=text)
        p.add_argument('--out', type=Path, default=ROOT / 'data' / 'recordings')
        p.add_argument('--json', action='store_true')
        p.add_argument('--timeout', type=float, default=45.0)
    a = ap.parse_args(argv)
    out = Path(a.out)

    def show(st):
        print(json.dumps(st, indent=1) if getattr(a, 'json', False) else render(st))
        return st['exit']

    if a.action == 'status':
        return show(status(out))
    if a.action == 'run':
        out.mkdir(parents=True, exist_ok=True)
        if a.detached:                                       # no console: keep tracebacks
            sys.stdout = sys.stderr = open(out / 'watchdog.err', 'a', encoding='utf-8', buffering=1)
        deadline = a.deadline or (time.time() + a.days * 86400.0 if a.days else None)
        child = json.loads(a.child_cmd) if a.child_cmd else None
        cmd_for = (lambda rem: child) if child else (lambda rem: recorder_cmd(a.mode, a.symbols, out, a.min_free_gb, rem))
        return Supervisor(cmd_for, out, interval=a.interval, stall_s=a.stall_s, min_free_gb=a.min_free_gb,
                          deadline=deadline, grace_s=a.grace_s, max_restarts=a.max_restarts, launch=a.launch).run()
    if a.action == 'start':
        deadline = time.time() + a.days * 86400.0 if a.days else None
        args = run_args_of(a, deadline)
        out.mkdir(parents=True, exist_ok=True)
        write_atomic(out / CONFIG, json.dumps({'run_args': args, 'launch': a.launch, 'saved_utc': iso(time.time())},
                                              indent=1) + '\n')
        st = start(args, out, a.launch)
        print(st.get('note') or f"spawned pid {st['spawned_pid']} via {st['method']}")
        return show(st)
    if a.action == 'stop':
        st = status(out)
        if st['health'] == 'NEVER_STARTED' or st['health'] in FINAL:
            return show(st)
        (out / STOP).write_text('stop\n', encoding='utf-8')
        hb = st['heartbeat']
        if not st['watchdog_alive'] and st['recorder_alive']:      # an orphan: ask the recorder directly
            (out / CHILD_STOP).write_text('stop\n', encoding='utf-8')
        end = time.time() + a.timeout
        while time.time() < end:
            st = status(out)
            gone = not same_process(hb.get('recorder_pid'), hb.get('recorder_create_time'))
            if st['health'] in FINAL or (not st['watchdog_alive'] and gone):
                break
            time.sleep(0.5)
        if not st['watchdog_alive']:
            for name in (STOP, CHILD_STOP):
                (out / name).unlink(missing_ok=True)
        return show(st)
    # ensure
    st = status(out)
    if st['health'] in ALIVE or st['health'] in FINAL:           # at work, or stopped on purpose: leave it
        return show(st)
    try:
        cfg = json.loads((out / CONFIG).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        print(f'no saved options ({out / CONFIG}): use `start` once')
        return 3
    st = start(cfg['run_args'], out, cfg.get('launch', 'auto'))
    print(f"restarted: pid {st.get('spawned_pid')} via {st.get('method')}")
    return show(st)


if __name__ == '__main__':
    raise SystemExit(main())
