"""Constants for the Schluter DITRA-HEAT integration."""
from datetime import timedelta

DOMAIN = "schluterditraheat"

# Configuration
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# API
API_BASE_URL = "https://schluterditraheat.com/api"
API_TIMEOUT = 30

# Update interval
SCAN_INTERVAL = timedelta(seconds=60)

# Static data cache refresh (polls between full refreshes; ~1 hour at 60s interval)
STATIC_REFRESH_INTERVAL_POLLS = 60

# Rate limit backoff
RATE_LIMIT_INITIAL_BACKOFF = timedelta(minutes=2)
RATE_LIMIT_MAX_BACKOFF = timedelta(minutes=16)
RATE_LIMIT_BACKOFF_FACTOR = 2

# Temperature limits (Celsius)
MIN_TEMP_C = 5.0
MAX_TEMP_C = 32.0

# Attributes
ATTR_DEVICE_ID = "device_id"
ATTR_IDENTIFIER = "identifier"
ATTR_GROUP_NAME = "group_name"
ATTR_LOCATION_NAME = "location_name"

# Modes
MODE_AUTO = "auto"
MODE_OFF = "off"
MODE_MANUAL = "manual"
MODE_FROST_SAFE = "frostProtection"

# Preset modes — only needed for modes that have no HVACMode enum equivalent.
# auto/manual/off map to HVACMode.AUTO/HEAT/OFF so HA owns those labels.
# Frost protection has no HVACMode, so it uses a plain string that must be
# declared here to avoid scattering the literal across the codebase.
PRESET_NONE = "none"
PRESET_FROST_PROTECTION = "frost_protection"