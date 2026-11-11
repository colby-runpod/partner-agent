"""Request-based routing contract shared with Operating Model section 5.1.

No quality threshold, new destination, outbound email or commercial approval.
The model identifies the transaction; this map determines the primary route.
"""
VERSION = '2026-11-11.2'
INTENTS = {
    'affiliate_promotion': 'Affiliate Program',
    'creator_sponsorship': 'Affiliate Program',
    'startup_own_credits': 'Startup Program',
    'portfolio_program': 'VC/Incubator',
    'supply_capacity': 'Supply Team',
    'event_support': 'Events/Sponsorship',
    'research_support': 'Research/University',
    'teaching_support': 'Research/University',
    'marketing_collaboration': 'Marketing',
    'media_appearance': 'Marketing',
    'customer_referral': 'Partnerships',
    'resale': 'Partnerships',
    'commercial_integration': 'Partnerships',
    'model_distribution': 'Partnerships',
    'implementation_services': 'Partnerships',
    'managed_services': 'Partnerships',
    'consultancy': 'Partnerships',
    'compute_purchase': 'Sales',
    'product_proposal': 'Product/Eng',
    'unowned_vendor': 'Review Needed',
    'unclear': 'Review Needed',
}
SUBTYPES = (
    'affiliate_organic', 'affiliate_paid_search', 'creator_paid_content',
    'startup_credits', 'portfolio_benefits',
    'supply_operating', 'supply_deploying', 'supply_predevelopment',
    'student_hackathon', 'research_hackathon', 'developer_workshop',
    'tech_conference', 'creative_festival', 'founder_showcase',
    'community_residency', 'event_prize_donation', 'other_event',
    'research_project', 'teaching_cohort', 'educational_workshop',
    'brand_campaign', 'co_marketing', 'media_guest',
    'customer_referral', 'gpu_broker', 'reseller_var', 'reseller_enterprise',
    'reseller_niche', 'reseller_gpu_platform', 'embedded_application',
    'commercial_integration', 'model_publisher', 'systems_integrator',
    'managed_service_provider', 'consultancy', 'compute_buyer',
    'product_proposal', 'unowned_vendor', 'unspecified',
)
INSTRUCTIONS = '''
Request-based routing contract (Operating Model 5.1):
Classify the requested transaction, not the sender's industry, name or form checkbox.
Return routing_context with intent, subtype, rationale, secondary_consultation,
commercial_role, event_date and decision_deadline. Use one primary intent from:
''' + ', '.join(INTENTS) + '''.
Subtype must be one of: ''' + ', '.join(SUBTYPES) + '''.
Rationale: one sentence grounded in the actual ask. Commercial role: who buys,
contracts and bills, or "Unknown". Secondary consultation: a short advisory note,
not a second route or posting instruction. Dates: YYYY-MM-DD only if explicitly
known, otherwise empty; never invent a year or deadline.

Precedence and boundaries:
- Selling GPU/site/power/hosting capacity to Runpod is supply_capacity, even small,
  unverified or pre-development supply. Broker offering capacity uses Supply;
  broker buying for a principal uses compute_purchase; broker seeking resale
  rights uses resale; broker introducing a direct customer uses customer_referral.
- compute_purchase is paying for compute/availability, even for a university or
  hackathon. Event credits/sponsorship are event_support even without a date,
  audience size or budget. Do not require missing details before routing.
- Hackathons, one-off sponsored workshops, conferences, festivals, showcases and
  donated event prizes are event_support. Research hackathons may note research
  consultation; do not create a second handoff. Ongoing teaching/cohorts and
  research projects use teaching_support/research_support, including independent
  researchers. A small educational workshop focused on curriculum rather than
  event sponsorship uses teaching_support. State mixed scope explicitly.
- A startup's own credits are startup_own_credits. Portfolio-wide benefits are
  portfolio_program. A VC rebilling portfolio companies is resale, not a program.
- Affiliate links/audience promotion (including paid search) are affiliate_promotion;
  paid creator content is creator_sponsorship. Named-customer introductions where
  Runpod contracts/bills are customer_referral. Media guest invitations and general
  campaigns/co-marketing are media_appearance/marketing_collaboration, not affiliate.
- Resale means the applicant bills customers for Runpod capacity. Distinguish VAR
  bundles, enterprise channels, niche/regional service and white-label GPU platforms.
  Enterprise/niche describe reach, not evidence of qualification. An application
  using compute is not automatically raw-compute resale; use embedded_application
  subtype and compute_purchase unless explicit resale rights/commercial integration
  are requested. Never imply resale consent, discounts or available program terms.
- Commercial connectors, joint distribution and model/API catalog partnerships use
  commercial_integration/model_distribution. Standalone technology proposed for
  Runpod's own product uses product_proposal; owner TBD, report to Colby.
- implementation_services delivers projects; managed_services operates workloads;
  consultancy advises buyers. Record whether they bring customers or ask Runpod
  to provide them. Vendors selling unrelated staffing/job ads/software to Runpod
  with no applicable specialist owner use unowned_vendor, not Product/Eng.
- Choose the dominant explicit ask. Describe secondary asks as consultation only.
  If ownership truly cannot be determined, use unclear with the specific ambiguity.

Qualification is separate: never raise referral scores merely for being referrals.
Use evidence of customer access, workload, authority and readiness. Reseller program
is being developed: scrutinize customer demand, differentiation, support/billing,
implementation burden and credible volume. Research feasibility and outputs,
event audience/support/deadline and supply readiness are type-specific evidence.
Complete available research before suggesting questions; unknown is not false.
Low score, missing verification, personal email, small scale and missing event
metadata never suppress specialist routing. Spam means clearly unrelated mass bait,
scam/phishing or nonsense, not a legitimate commercial/creator/sponsorship pitch.
'''


def normalize(raw, fallback):
    """Validate new metadata without reinterpreting legacy cards or saved routes."""
    if not isinstance(raw, dict):
        return fallback, {}
    intent = raw.get('intent')
    if intent not in INTENTS:
        return fallback, {}
    import datetime as dt
    def clean(key, limit=300):
        value = raw.get(key)
        return str(value).strip()[:limit] if isinstance(value, str) else ''
    context = {key: clean(key) for key in ('rationale', 'secondary_consultation', 'commercial_role')}
    context.update(intent=intent, subtype=raw.get('subtype') if raw.get('subtype') in SUBTYPES else 'unspecified')
    for key in ('event_date', 'decision_deadline'):
        value = clean(key, 10)
        try:
            if raw.get(key) != value: raise ValueError('Not an exact date')
            dt.date.fromisoformat(value)
        except (ValueError, TypeError): value = ''
        context[key] = value
    context['policy_version'] = VERSION
    return INTENTS[intent], context


def detail_lines(context):
    if not context: return []
    labels = [('subtype','Request subtype'), ('commercial_role','Commercial role'),
              ('rationale','Routing reason'), ('secondary_consultation','Secondary consultation'),
              ('event_date','Event date'), ('decision_deadline','Decision deadline')]
    return [(label, context[key].replace('_', ' ') if key == 'subtype' else context[key])
            for key, label in labels if context.get(key)]
