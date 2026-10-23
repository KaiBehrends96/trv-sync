"""Static constants and defaults for trv_sync."""

DOMAIN = "trv_sync"

# Guard defaults, matching the approved plan; all of them are per-room options.
DEFAULT_DEADBAND = 0.3
DEFAULT_WRITE_RATE_LIMIT_MINUTES = 10.0
DEFAULT_STALENESS_MINUTES = 30.0
DEFAULT_UPDATE_INTERVAL_SECONDS = 60
DEFAULT_TRIM = 0.0

# Used only when a TRV's offset number entity exists but does not expose
# min/max attributes (defensive fallback; real ZHA number entities carry them).
DEFAULT_OFFSET_LIMITS = (-6.0, 6.0)

# Non-configurable safety knobs.
PUSH_FAIL_MUTE_STREAK = 3
MUTE_RETRY_MULTIPLIER = 6  # x update interval before a muted TRV is retried
# Two temperatures closer than this count as "same value"; keeps float noise
# and 0.5-step UI roundings from being seen as changes.
TARGET_TOLERANCE = 0.05

# A TRV's offset register number entity. ZHA derives its entity ids from the
# climate entity: climate.heizzimmer1_fenster_thermostat ->
# number.heizzimmer1_fenster_lokaler_temperatur_offset.
OFFSET_NUMBER_SUFFIX = "_lokaler_temperatur_offset"
TRV_ENTITY_SUFFIX = "_thermostat"