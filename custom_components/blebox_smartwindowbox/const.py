DOMAIN = "blebox_smartwindowbox"

CONF_HOST = "host"
CONF_NAME = "name"
DEFAULT_NAME = "BleBox smartWindowBox"

PLATFORMS = ["cover", "binary_sensor"]

UPDATE_INTERVAL_SECONDS = 5
MOVING_UPDATE_INTERVAL_SECONDS = 1

# Motor commands
CMD_UP = "u"       # moves toward position 0 (open) on your device
CMD_DOWN = "d"     # moves toward position 100 (close) on your device
CMD_STOP = "s"
CMD_NEXT = "n"
CMD_FAV = "f"

# Emulated position control tuning
POSITION_TOLERANCE = 1          # stop when within 1%
POLL_INTERVAL_SEC = 0.35        # fast polling while moving to target
EXTRA_STOP_DELAY_SEC = 0.15     # optional second stop to reduce coasting
