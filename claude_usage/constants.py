APP_VERSION = "0.6.1"
DEFAULT_SYNC_INTERVAL_MINUTES = 30
DEFAULT_ENROLL_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/enroll"
DEFAULT_INGEST_URL = "https://yeokmzmmldqjngwtrfso.supabase.co/functions/v1/ingest"
DEFAULT_SYNC_DAYS = 90

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
