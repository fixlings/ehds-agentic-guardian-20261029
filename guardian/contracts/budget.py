"""Hard bounds for one agentic request. Not configurable per call."""

MAX_TOOL_STEPS = 3
MAX_MODEL_RETRIES = 1
MODEL_TIMEOUT_S = 20.0
TOOL_TIMEOUT_S = 5.0
REQUEST_DEADLINE_S = 55.0
SUPPRESSION_THRESHOLD = 5
FIXTURE_VERSION = "2026-10-29"

# EU regional inference only. Never use https://api.mistral.ai for this module.
MISTRAL_EU_BASE_URL = "https://api.eu.mistral.ai"
DEFAULT_MODEL = "mistral-small-2603"
CANDIDATE_MODEL = "mistral-medium-3-5"

# Official Track 3 rule: counts below 10 are shown as "<10".
TRACK3_SUPPRESSION_THRESHOLD = 10
