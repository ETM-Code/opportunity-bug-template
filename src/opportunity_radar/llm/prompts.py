"""LLM prompt templates."""

CLASSIFY_PROMPT = """Analyze the following content and determine if it contains any opportunities relevant to a tech professional looking for:
- Residencies, fellowships, or research programs
- Hackathons or competitions
- Internships or job openings
- Grants or funding opportunities
- Accelerator or founder programs

Content:
{content}

Respond with a JSON object:
{{
    "contains_opportunity": true/false,
    "opportunity_types": ["residency", "hackathon", "fellowship", "job", "grant", "internship", "accelerator"],
    "confidence": 0.0-1.0,
    "brief_reason": "One sentence explaining why"
}}

Only respond with the JSON, no other text."""


EXTRACT_PROMPT = """Extract SPECIFIC opportunities from this content. Focus on concrete, named opportunities with clear details.

IMPORTANT:
- Extract MULTIPLE opportunities if the content lists several (like a newsletter with job listings)
- Each opportunity must be a SPECIFIC role, program, or event - NOT a generic "browse our jobs" page
- Skip generic content like "explore careers at X" or "view all open positions"
- Only extract opportunities that have a clear title and some concrete details

Content:
{content}

Return a JSON array of opportunities:
[
  {{
    "title": "Specific name of role/program (e.g. 'Software Engineer - AI Safety' not 'Careers at Company')",
    "organization": "Company or institution",
    "url": "Direct link to this specific opportunity",
    "application_url": "Direct application link if available",
    "type": "residency|hackathon|fellowship|job|grant|internship|accelerator|competition",
    "deadline": "YYYY-MM-DD or null",
    "stipend_amount": number or null (for hackathons: total prize pool or top prize),
    "stipend_currency": "USD or other",
    "travel_support": "none|partial|full|unknown",
    "location": "City, Country or Remote/Online",
    "is_remote": true/false/null,
    "eligibility": "Brief requirements",
    "summary": "2-3 sentence description of what makes this specific opportunity unique",
    "highlights": ["Specific benefit 1", "Specific benefit 2"],
    "prize_details": "For hackathons: prize breakdown if available (e.g. '1st: $10k, 2nd: $5k')"
  }}
]

Return [] if no SPECIFIC opportunities found (don't extract generic job board pages).
Only respond with the JSON array, no other text."""


SCORE_PROMPT = """Score this opportunity for the candidate using this STRICT rubric.

CANDIDATE:
{profile}

OPPORTUNITY:
Title: {title}
Organization: {organization}
Type: {type}
Location: {location}
Remote: {is_remote}
Deadline: {deadline}
Stipend: {stipend}
Travel Support: {travel_support}
Summary: {summary}
Eligibility: {eligibility}

SCORING RULES (be strict, don't inflate scores):

relevance_score (0.0-1.0):
- 0.9-1.0: Perfect match - frontier AI lab (OpenAI/Anthropic/DeepMind/xAI), AI safety, systems/hardware, top accelerator/fellowship, OR high-prize hackathon in AI/tech
- 0.7-0.89: Strong match - AI/ML role at reputable org, well-funded research program, relevant engineering role, OR quality hackathon with prizes/travel support
- 0.5-0.69: Moderate match - generally relevant tech role/hackathon but not core interest area
- 0.3-0.49: Weak match - tangentially related, wrong level (PhD required when undergrad), or missing key requirements
- 0.0-0.29: Poor match - unrelated field, wrong geography with no travel support, or fundamental eligibility mismatch

MUST PENALIZE (reduce score by 0.2-0.4 each):
- Requires PhD/Masters when candidate is undergrad
- US-only with no visa/travel support for international applicants
- Unpaid with no travel support (for roles requiring relocation)
- Completely unrelated field (bio/chemistry/non-tech)

MUST BOOST (add 0.1-0.2 each):
- Frontier AI lab (OpenAI, Anthropic, DeepMind, xAI, Meta AI)
- Paid stipend + travel covered
- AI safety or alignment focus
- Hardware/systems/infrastructure focus
- Explicitly open to undergrads or "any career stage"
- Hackathon with significant prizes (>$1k) or travel support
- AI/ML focused hackathon or competition
- Hackathon sponsored by major tech companies

prestige_score (0.0-1.0):
- 0.9-1.0: Top-tier (FAANG AI, frontier labs, YC, Stanford/MIT/ETH/Cambridge)
- 0.7-0.89: Strong (well-known tech companies, top 50 universities)
- 0.5-0.69: Solid (recognized organizations, funded programs)
- 0.3-0.49: Moderate (smaller orgs, newer programs)
- 0.0-0.29: Unknown/unestablished

HIGH VALUE SIGNALS (candidate's priorities):
{high_value_signals}

LOW VALUE SIGNALS (candidate's dealbreakers):
{low_value_signals}

Respond with JSON only:
{{
    "relevance_score": 0.0-1.0,
    "prestige_score": 0.0-1.0,
    "reasoning": "2-3 sentences justifying scores with specific reasons",
    "matched_high_signals": ["matched signals from above"],
    "matched_low_signals": ["matched signals from above"],
    "recommendation": "strong_apply|apply|maybe|skip"
}}

Recommendations: strong_apply (>0.8 relevance), apply (0.6-0.8), maybe (0.4-0.6), skip (<0.4 or dealbreaker present)"""


DIGEST_SUMMARY_PROMPT = """Write a brief, punchy one-liner for this opportunity that would excite a tech-focused student/founder.

Opportunity: {title} at {organization}
Type: {type}
Key details: {highlights}

Write a single compelling sentence (max 100 chars) that captures why this is exciting. Be specific, not generic. No emojis."""
