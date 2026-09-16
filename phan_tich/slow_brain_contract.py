"""Slow-brain verdict contract (Astra Q4/Q5 handoff) — offline validator only.

No runtime wiring in this change: this module defines and validates the
hourly JSON verdict a future slow-brain may emit. Policy: verdict can only
BLOCK, never override ZEPHR/RUNE. Stale/malformed/future verdict -> block.

v2 fields (Q4 handoff): available_at / published_at / reason / timeframe /
max_source_age_s. v1 verdicts (10 fields) still validate.
"""
import math


REQUIRED_FIELDS = ('schema_version', 'symbol', 'regime', 'veto', 'conviction',
                   'as_of_ts', 'generated_at', 'expires_at', 'data_cutoff',
                   'model_version')
# v2 optional lineage fields — required when schema_version >= 2.
V2_FIELDS = ('available_at', 'published_at', 'reason', 'timeframe',
             'max_source_age_s')
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
    # v2 lineage: available/published ordering + source-age bound.
    # v1 verdicts (schema_version < 2) skip this — backward compatible.
    try:
        schema_v = int(verdict.get('schema_version', 1))
    except (TypeError, ValueError):
        return False, 'NON_NUMERIC_TIMESTAMP'
    if schema_v >= 2:
        if any(k not in verdict for k in V2_FIELDS):
            return False, 'MISSING_V2_FIELDS'
        try:
            available = float(verdict['available_at'])
            published = float(verdict['published_at'])
            max_age = float(verdict['max_source_age_s'])
        except (TypeError, ValueError):
            return False, 'NON_NUMERIC_TIMESTAMP'
        if not all(math.isfinite(v) for v in (available, published, max_age)):
            return False, 'NON_FINITE_FIELD'
        # as_of <= available <= published <= decision(intent); rerun must not
        # refresh source age: source is stale if older than max_source_age_s.
        if not (as_of <= available <= published <= now):
            return False, 'LINEAGE_ORDER_VIOLATION'
        if max_age <= 0 or (now - as_of) > max_age:
            return False, 'SOURCE_TOO_OLD'
        if not isinstance(verdict.get('reason'), str) or not verdict['reason']:
            return False, 'MISSING_REASON'
        if not isinstance(verdict.get('timeframe'), str) or not verdict['timeframe']:
            return False, 'MISSING_TIMEFRAME'
    if verdict.get('regime') not in REGIMES:
        return False, 'UNKNOWN_REGIME'
    if verdict.get('veto') is True or verdict.get('regime') in ('CHOP', 'TOXIC'):
        return False, 'SLOW_BRAIN_BLOCK'
    if not 0 <= conviction <= 1:
        return False, 'BAD_CONVICTION'
    return True, 'SLOW_BRAIN_PASS'
