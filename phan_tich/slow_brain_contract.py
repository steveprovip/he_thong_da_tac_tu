"""Slow-brain verdict contract (Astra Q4) — offline validator only.

No runtime wiring in this change: this module defines and validates the
hourly JSON verdict a future slow-brain may emit. Policy: verdict can only
BLOCK, never override ZEPHR/RUNE. Stale/malformed/future verdict -> block.
"""
import math


REQUIRED_FIELDS = ('schema_version', 'symbol', 'regime', 'veto', 'conviction',
                   'as_of_ts', 'generated_at', 'expires_at', 'data_cutoff',
                   'model_version')
REGIMES = ('TREND', 'CHOP', 'TOXIC')


def validate_verdict(verdict, intent_ts):
    """Return (allow_entry: bool, reason: str). Never raises on bad input."""
    if not isinstance(verdict, dict):
        return False, 'MALFORMED_VERDICT'
    if any(k not in verdict for k in REQUIRED_FIELDS):
        return False, 'MISSING_FIELDS'
    try:
        as_of = float(verdict['as_of_ts'])
        generated = float(verdict['generated_at'])
        expires = float(verdict['expires_at'])
        cutoff = float(verdict['data_cutoff'])
        conviction = float(verdict['conviction'])
        now = float(intent_ts)
    except (TypeError, ValueError):
        return False, 'NON_NUMERIC_TIMESTAMP'
    if not all(math.isfinite(v) for v in (as_of, generated, expires, cutoff, conviction, now)):
        return False, 'NON_FINITE_FIELD'
    # Causal chain: data_cutoff <= as_of <= generated < intent < expires
    if not (cutoff <= as_of <= generated < now < expires):
        return False, 'STALE_OR_FUTURE_VERDICT'
    if verdict.get('regime') not in REGIMES:
        return False, 'UNKNOWN_REGIME'
    if verdict.get('veto') is True or verdict.get('regime') in ('CHOP', 'TOXIC'):
        return False, 'SLOW_BRAIN_BLOCK'
    if not 0 <= conviction <= 1:
        return False, 'BAD_CONVICTION'
    return True, 'SLOW_BRAIN_PASS'
