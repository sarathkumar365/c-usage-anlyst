APP_VERSION = "0.7.0"
DEFAULT_SYNC_INTERVAL_MINUTES = 30
DEFAULT_ENROLL_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll"
DEFAULT_INGEST_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest"
DEFAULT_SYNC_DAYS = 90

CLAUDE_CHROME_EXTENSION_ID = "fcoeoabgfenejglbffodgkkbkcdhcgfn"
CLAUDE_CODE_EXTENSION_PREFIX = "anthropic.claude-code"
PLAN_USAGE_HISTORY_FILE = "plan-usage-history.json"
PLAN_USAGE_HISTORY_VERSIONS = {2}
STATUSLINE_SAMPLES_FILE = "plan-samples.jsonl"
STATUSLINE_MIN_INTERVAL_SECONDS = 60

MODEL_PRICES_USD_PER_M = {
    # Best-effort reference prices. Unknown/new aliases are intentionally $0.
    # These are NOT subscription billing amounts.
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-fable-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
