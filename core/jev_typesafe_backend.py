"""JEV / TypeSafe AI backend for core.jev_gatekeeper.JevGatekeeper (optional, veto-only).

Real SDK surface (F:\\JEV_AI\\typesafe-sdk-python, verified by reading the source):
    from typesafe_sdk import TypeSafeClient, Noul, Score
    client.system_one(state={...}, questions={'name': Noul(...)|Score(...)}, timeout=s)
    -> SystemOneResponse.answers[name].noul  (P(yes) in [0,1])
       SystemOneResponse.answers[name].score (expected rubric level)
(README_TONG_QUAN.md shows `client.evaluate(context, question, options)`; that
method does not exist in the SDK source.)

It is a network API (https://api.typesafe.ai, needs TYPESAFE_API_KEY), so a
round trip from Asia is RTT-bound (~100-300 ms), not 5-15 ms. The local
deterministic gates answer in ~2 us. Therefore:
  * the per-tick gate keeps the 10 ms budget and the local rule answers;
  * run_alpha_instant --jev uses JEV once per candidate with a 2 s budget as
    a second-opinion VETO (it can lower a score or add a block, never grant).
"""
import os
import sys

DEFAULT_SDK_PATH = r'F:\JEV_AI\typesafe-sdk-python\src'
SCORE_LEVELS = [f'{i}/10: gross edge about {i * 0.35:.1f}x all-in cost' for i in range(11)]


def load_client(api_key=None, sdk_path=None, timeout_s=2.0):
    """TypeSafeClient or None when the SDK, its deps or the API key are missing."""
    key = api_key or os.environ.get('TYPESAFE_API_KEY')
    if not key:
        return None
    path = sdk_path or os.environ.get('JEV_SDK_PATH') or DEFAULT_SDK_PATH
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
    try:
        from typesafe_sdk import TypeSafeClient
    except ImportError:
        return None
    return TypeSafeClient(api_key=key, timeout=timeout_s)


def make_backend(client, timeout_s=2.0):
    """Adapter: (kind, payload) -> bool | float for JevGatekeeper."""
    from typesafe_sdk import Noul, Score

    questions = {
        'adverse': {'adverse': Noul(
            instructions='A passive limit order rests at the best price on this side. Given the order-book '
                         'depth and recent aggressor flow, will the book trade THROUGH this price within seconds '
                         '(adverse selection)?',
            criteria={'true': 'queue/flow leans against the resting side', 'false': 'queue/flow supports it'})},
        'score': {'score': Score(
            instructions='Rate how clearly the expected gross edge of this crypto regime exceeds its all-in '
                         'trading cost (fees+spread+slippage). 8.5 or more means gross > 3x cost.',
            criteria=SCORE_LEVELS)},
    }

    def backend(kind, payload):
        state = {k: (v if isinstance(v, (int, float, str, bool)) or v is None else str(v))
                 for k, v in payload.items()}
        response = client.system_one(state=state, questions=questions[kind], timeout=timeout_s)
        answer = response.answers[kind]
        return bool(answer.noul >= .5) if kind == 'adverse' else float(answer.score)

    return backend
