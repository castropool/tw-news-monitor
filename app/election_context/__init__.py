FACT_STATUS = frozenset({
    'verified', 'multi_source_verified', 'candidate_claim', 'party_claim',
    'media_interpretation', 'poll_result', 'analytical_inference',
    'disputed', 'pending_verification', 'superseded',
})

EVENT_TYPES = frozenset({
    'candidate_announcement', 'candidate_withdrawal', 'primary_registration',
    'primary_result', 'party_nomination', 'endorsement', 'party_integration',
    'faction_conflict', 'alliance_proposal', 'alliance_agreement', 'poll_release',
    'policy_proposal', 'campaign_launch', 'fundraising', 'governance_event',
    'disaster_response', 'scandal_allegation', 'judicial_event',
    'campaign_attack', 'joint_campaign', 'candidate_status_change',
    'primary_procedure', 'primary_debate',
})

ACTOR_TYPES = frozenset({'candidate', 'politician', 'party', 'faction', 'organization', 'media'})

SIGNIFICANCE_MIN = 0
SIGNIFICANCE_MAX = 100

def validate_fact_status(v: str) -> str:
    if v not in FACT_STATUS:
        raise ValueError(f'invalid fact_status: {v!r}')
    return v

def validate_event_type(v: str) -> str:
    if v not in EVENT_TYPES:
        raise ValueError(f'invalid event_type: {v!r}')
    return v

def validate_actor_type(v: str) -> str:
    if v not in ACTOR_TYPES:
        raise ValueError(f'invalid actor_type: {v!r}')
    return v

def validate_significance(v: int) -> int:
    if not (SIGNIFICANCE_MIN <= v <= SIGNIFICANCE_MAX):
        raise ValueError(f'significance_score must be 0..100, got {v}')
    return v
