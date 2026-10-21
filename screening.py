"""Deterministic inbound policy, version 2026-10-20. No network or external writes."""

VERSION = '2026-10-20'
DIMENSIONS = ('A', 'S', 'I', 'C', 'V')
RESEARCH = ('registry', 'linkedin', 'directories', 'press', 'gmail')
ROUTES = {'vendor', 'sales', 'credits', 'startup', 'affiliate', 'subscale_supply'}


def evaluate(facts):
    scores = facts.get('scores', {})
    if set(scores) != set(DIMENSIONS) or any(type(scores[k]) is not int or not 0 <= scores[k] <= 3 for k in DIMENSIONS):
        raise ValueError('Screening needs five integer scores between 0 and 3')
    a, s, i, c, v = (scores[k] for k in DIMENSIONS)
    total = 3*a + 2*s + i + 3*c + 2*v
    result = dict(version=VERSION, scores=scores.copy(), total=total, tier='', reason='', priority=False)
    def finish(tier, reason):
        result.update(tier=tier, reason=reason, priority=tier == 'Act now')
        return result
    risk = facts.get('risk', 'none')
    if risk not in ('none', 'policy_review', 'misrepresentation', 'spam'):
        raise ValueError('Unknown risk gate')
    if risk != 'none':
        if not str(facts.get('risk_evidence', '')).strip():
            return finish('Needs research', 'Risk flag needs supporting evidence; nationality alone is not a finding')
        return finish('Hold for Colby' if s == 3 else 'DQ', 'Risk gate: ' + risk)
    route = facts.get('wrong_door', '')
    if route:
        if route not in ROUTES:
            raise ValueError('Unknown routing category')
        return finish('Route', route)
    if facts.get('predevelopment_supply') is True:
        return finish('DQ', 'Supply lacks secured site/power/hardware; invite re-entry when secured')
    # A short digest must not become a low score without the original form.
    if facts.get('blank_payload') is True and facts.get('hubspot_checked') is not True:
        return finish('Needs research', 'Read the original HubSpot form before qualifying a blank/short digest')
    referred = facts.get('teammate_referred') is True
    own_domain = facts.get('own_domain') is True
    if total >= 22 or c == 3 or (v == 3 and s >= 2 and a >= 1) or (referred and c >= 2):
        return finish('Act now', 'Priority threshold met')
    floors = []
    if a >= 2 and own_domain and (v >= 2 or c >= 2): floors.append('F1')
    if s >= 2 and own_domain and a <= 1: floors.append('F2')
    if referred: floors.append('F3')
    if total >= 15 or floors:
        return finish('Qualify', ', '.join(floors) or 'Score 15-21')
    if facts.get('unverifiable') is True:
        checks = facts.get('research', {})
        if any(not isinstance(checks.get(k), str) or not checks[k].strip() or
               checks[k].strip().lower().startswith(('unavailable', 'not checked', 'pending')) for k in RESEARCH):
            return finish('Needs research', 'Unverifiability requires registry, LinkedIn, directories, press and Gmail checks')
    return finish('DQ', 'Score at most 14 and no floor')


def notes(result):
    return 'Screening %s: %s; total %s/33; %s (%s).' % (
        result['version'], ', '.join('%s=%s' % (k, result['scores'][k]) for k in DIMENSIONS),
        result['total'], result['tier'], result['reason'])


def apply(review, facts):
    result = evaluate(facts)
    review['screening'] = result
    # The old select remains a compatibility bucket, never the 0-33 total.
    mapping = {'Act now': ('4', 'pursue'), 'Qualify': ('3', 'triage'), 'DQ': ('DQ', 'do_not_pursue'),
               'Route': ('3', 'triage'), 'Hold for Colby': ('3', 'triage'), 'Needs research': ('3', 'triage')}
    review['score'], review['decision'] = mapping[result['tier']]
    # DQ select is reserved for spam; ordinary disqualification retains a number.
    if result['tier'] == 'DQ' and facts.get('risk') != 'spam':
        review['score'] = '2'
    review['why'] = notes(result)
    review['next_step'] = {'Act now': 'Review the priority lead in the shared review queue.',
        'Qualify': 'Draft 1-3 specific probe questions for daily batch approval.',
        'DQ': 'Record the reason. Only eligible verified, non-spam, non-supply leads get a soft-decline draft after seven days; include a re-entry line and never send.',
        'Route': 'Route to ' + result['reason'] + '; this is not a partnership quality rejection.',
        'Hold for Colby': 'Hold for Colby to review the documented risk before any outreach.',
        'Needs research': result['reason']}[result['tier']]
    return review
