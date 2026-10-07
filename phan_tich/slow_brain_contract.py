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


# TradingAgents v0.4.0 bridge. TradingAgents-main stays upstream-clean; its
# SignalProcessor.process_signal() returns one of the 5-tier ratings or REVIEW,
# and this maps that string onto a v2 verdict that can only BLOCK:
#   Buy/Sell           -> TREND, conviction 1.0
#   Overweight/Under.  -> TREND, conviction 0.6
#   Hold               -> CHOP  (block: no directional debate outcome)
#   REVIEW / unknown   -> veto  (block: an unparsed debate is not a neutral one)
_RATING_MAP = {'buy': ('TREND', 1.0, 'LONG'), 'overweight': ('TREND', .6, 'LONG'),
               'sell': ('TREND', 1.0, 'SHORT'), 'underweight': ('TREND', .6, 'SHORT'),
               'hold': ('CHOP', 0.0, 'FLAT')}


def verdict_from_rating(rating, symbol, as_of_ts, generated_at, ttl_s=3600.0,
                        model_version='tradingagents-0.4.0', timeframe='1h'):
    """Build a schema-v2 slow-brain verdict from a TradingAgents rating string."""
    key = str(rating or '').strip().lower()
    regime, conviction, bias = _RATING_MAP.get(key, ('TOXIC', 0.0, 'FLAT'))
    as_of, generated, ttl = float(as_of_ts), float(generated_at), float(ttl_s)
    return {'schema_version': 2, 'symbol': symbol, 'regime': regime,
            'veto': key not in _RATING_MAP, 'conviction': conviction, 'bias': bias,
            'as_of_ts': as_of, 'generated_at': generated, 'expires_at': generated + ttl,
            'data_cutoff': as_of, 'model_version': model_version,
            'available_at': generated, 'published_at': generated,
            'reason': f'TradingAgents rating={rating!s}', 'timeframe': timeframe,
            'max_source_age_s': ttl}
