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
MODE_MANUAL = "autoBypass"  # For manual temperature override
