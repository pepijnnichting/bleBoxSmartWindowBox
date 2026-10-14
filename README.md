## Installation (HACS)

1. Open **HACS** in Home Assistant.
2. Go to **Integrations**.
3. Click the **⋮** (top right) → **Custom repositories**.
4. Add this repository URL:

   `https://github.com/Sjnaaf/bleBoxSmartWindowBox`

5. Choose **Category: Integration**.
6. Click **Add**.
7. Search for **BleBox smartWindowBox** in HACS and install it.
8. Restart Home Assistant.
9. Add the integration via: **Settings → Devices & Services → Add integration**.

---
---

# BleBox smartWindowBox – Home Assistant Integration

A fully featured **custom Home Assistant integration** for the **BleBox smartWindowBox** ([API level 20180604](https://technical.blebox.eu/openAPI_smartWindowBox/openAPI_smartWindowBox_20180604.html)).

This integration provides:

* ✅ Cover entity (open / close / stop)
* ✅ Emulated **set position (0–100%)**
* ✅ Favorite position support
* ✅ Next step support
* ✅ Rain sensor as binary sensor
* ✅ Real-time movement metadata
* ✅ Progress %, remaining time & direction tracking
* ✅ Proper Home Assistant entity services with UI selectors

---

## Features

### Cover Entity

The smartWindowBox motor is exposed as a standard Home Assistant **cover entity**:

* `Open`
* `Close`
* `Stop`
* `Set position (0=closed, 100=open)`

For intermediate positions, the integration emulates position setting because the BleBox API does **not** support direct position setting:

1. Sending open or close command
2. Polling device state
3. Stopping the motor once the target position is reached

Fully closed (0%) and fully open (100%) use the native close/open commands. The device completes its calibrated travel without an early stop based on a reported percentage.

All exposed position attributes use the Home Assistant scale (0% closed, 100% open). `device_position` retains the raw BleBox scale (0% open, 100% closed). Existing automations using numeric positions must invert their targets and comparisons (`100 - old_position`) when upgrading.

A new cover command cancels any previous intermediate-position controller before
sending the new command. Stop, favorite, next-step, and new position requests all
take precedence over the old target. Cancelling an active position-control
service or unloading the integration also cancels the controller and attempts
to stop the motor.

If position control loses contact with the device or exceeds its travel timeout,
it attempts a stop and reports an error. A stop cannot be guaranteed while the
device is unreachable; the original error is preserved if the stop attempt also
fails.

---

### Movement Metadata

While the window is moving (open, close, favorite, or manual movement), the following attributes are available:

| Attribute               | Description                    |
| ----------------------- | ------------------------------ |
| `moving`                | True while motor is active     |
| `move_direction`        | `opening` or `closing`         |
| `move_target_position`  | Desired target                 |
| `move_start_position`   | Starting position              |
| `move_elapsed_s`        | Seconds since movement started |
| `estimated_total_s`     | Estimated full travel time     |
| `estimated_remaining_s` | Remaining estimated time       |
| `move_progress_pct`     | Movement progress (0–100%)     |

These update live during movement.

When the motor changes direction or target, progress starts from the newly
observed position and the elapsed time and direction-specific travel estimate
are reset.

Device state is refreshed every second while a motor is moving and every five
seconds while idle. Commands trigger an immediate refresh. Intermediate-position
control also publishes each direct device reading to Home Assistant.

### Apple Home / HomeKit Bridge

Use Home Assistant 2026.10 or newer when exposing the cover through HomeKit
Bridge. In 2026.9.3 the bridge can retain an old target position during movement
started outside Apple Home, causing iOS to display the opposite direction even
when Home Assistant reports the correct direction. The bridge correction is in
Home Assistant; updating this custom integration alone does not fix that issue.

If a movement stops early from Apple Home, enable both loggers temporarily in
Home Assistant **Developer Tools → Actions**:

```yaml
action: logger.set_level
data:
  custom_components.blebox_smartwindowbox: debug
  homeassistant.components.homekit: debug
```

Reproduce one movement and inspect the logs for the requested Home Assistant
position, native command, and stop reason (target reached, cancellation,
timeout, or connection failure). Include the HomeKit lines immediately before
the stop when reporting the issue. Debug logging does not change motor behavior.
After collecting the logs, run the same action with both levels set to `warning`
or restart Home Assistant to restore the configured logging levels.

---

### Rain Sensor

The built-in rain sensor is exposed as a:

```
binary_sensor.<device_name>_rain
```

* ON = Rain detected
* OFF = No rain

Additional attributes:

* `trend`
* `elapsedTimeS`
* `state`
* `iconSet`

---

### Entity Services

The integration registers proper entity services with UI selectors:

| Service                              | Description                          |
| ------------------------------------ | ------------------------------------ |
| `blebox_smartwindowbox.favorite`     | Move to configured favorite position |
| `blebox_smartwindowbox.next_step`    | Perform next-step movement           |
| `blebox_smartwindowbox.set_position` | Move to specific position (0–100)    |

These appear in **Developer Tools → Services**.

---

# Installation

## Method 1 — Manual Installation (Recommended)

1. Download or clone this repository.
2. Copy the folder:

```
custom_components/blebox_smartwindowbox/
```

into your Home Assistant configuration directory:

```
config/custom_components/
```

Your final structure should look like:

```
config/
└── custom_components/
    └── blebox_smartwindowbox/
        ├── __init__.py
        ├── api.py
        ├── binary_sensor.py
        ├── config_flow.py
        ├── const.py
        ├── coordinator.py
        ├── cover.py
        ├── manifest.json
        ├── services.yaml
        ├── strings.json
        └── translations/
            └── en.json
```

3. Restart Home Assistant:

   * **Settings → System → Restart**

---

# Setup in Home Assistant

After restart:

1. Go to **Settings → Devices & Services**
2. Click **Add Integration**
3. Search for:

```
BleBox smartWindowBox
```

4. Enter the IP address or hostname of your device
5. Finish setup

The integration will automatically detect:

* The motor
* The rain sensor

---

# How To Use

## Using the Cover

The device appears as a standard cover entity:

```
cover.motor_1
```

You can:

* Open
* Close
* Stop
* Drag the position slider

Position scale:

```
0   = Fully Closed
100 = Fully Open
```

---

## Using Favorite Position

Go to:

```
Developer Tools → Services
```

Select:

```
blebox_smartwindowbox.favorite
```

Choose your cover entity and run.

---

## Using Set Position Service

Service:

```
blebox_smartwindowbox.set_position
```

You will get a slider selector (0–100).

Example:

```
Position: 60
```

The integration will:

* Move in correct direction
* Track progress
* Stop exactly at the target

---

## Viewing Movement Progress

Go to:

```
Developer Tools → States
```

Open your cover entity and watch:

* `move_progress_pct`
* `estimated_remaining_s`
* `move_elapsed_s`

These update live while the window moves.

---

# Tuning & Advanced Settings

Movement precision is controlled by:

```python
POSITION_TOLERANCE = 1
POLL_INTERVAL_SEC = 0.20
```

Lower polling interval increases precision but increases API traffic.

Default polling interval while moving:

```python
POLL_INTERVAL_SEC = 0.20
```

This value has been tuned for reliable real-world accuracy.

---

# State Mapping (Based on Real Device Behavior)

| State | Meaning                      |
| ----- | ---------------------------- |
| 0     | Closing                      |
| 1     | Opening                      |
| 2     | Idle (intermediate position) |
| 3     | Idle (fully closed)          |
| 4     | Idle (fully open)            |

---

# Compatibility

* BleBox smartWindowBox
* API level 20180604
* Single motor configuration (current version)

---

# Known Limitations

* Position control is emulated (not native in API)
* Accuracy depends on motor calibration
* Rapid repeated commands may slightly affect precision

---

# Development checks

With Home Assistant installed in the active Python environment, run the cover
regression tests from the repository root:

```sh
python -m unittest discover -s tests -v
```

These cover the Home Assistant position scale, intermediate movements, and native
endpoint commands. They use simulated responses; physical travel still needs to
be verified on a real device. The GitHub workflow also runs Hassfest validation.

---

# Future Improvements (Ideas)

* HACS support
* Multi-motor support
* Dedicated progress sensor entity
* Configurable precision via UI
* Automatic overshoot compensation tuning

---

# License

MIT License
