# HVAC Stability

A Python CLI tool for monitoring and managing HVAC device settings using the Kumo cloud API. This tool helps ensure your HVAC devices are following their scheduled setpoints and can automatically fix discrepancies.

## Features

- **Device Discovery**: Automatically discover and list all HVAC devices
- **Schedule Monitoring**: Check if devices are following their programmed schedules
- **Bulk Operations**: Check or fix settings across all devices at once
- **Temperature Conversion**: Support for both Fahrenheit and Celsius
- **Detailed Reporting**: Clear status reports with critical vs minor issues
- **Automated Fixes**: Automatically adjust device settings to match schedules

## Installation

Use [uv](https://astral.sh/uv/) and `uvx`:

`uvx hvac-stability --help`

## Quick Start

### 1. Login and Setup

```bash
# Login to Kumo cloud service
uvx hvac-stability login

# List all discovered devices
uvx hvac-stability list --verbose
```

### 2. Check Device Status

```bash
# Check all devices at once
uvx hvac-stability check-device-settings all

# Check a specific device
uvx hvac-stability check-device-settings "Device Name"

# Check with exit codes for automation
uvx hvac-stability check-device-settings all --exit-code
```

### 3. Fix Issues

```bash
# Fix all devices that are out of sync
uvx hvac-stability fix-device-settings all

# Fix a specific device
uvx hvac-stability fix-device-settings "Device Name"

# Preview changes without applying them
uvx hvac-stability fix-device-settings all --dry-run
```

## Example Usage

### Checking All Devices

```bash
$ uvx hvac-stability check-device-settings all
Checking all 5 device(s)...

(1/5) Checking Living Room (ABC123)
⚠️ Living Room has 1 minor issue(s) (no critical problems)

(2/5) Checking Office (DEF456)
✅ Office is in sync!

(3/5) Checking Bedroom 1 (GHI789)
✅ Bedroom 1 is in sync!

(4/5) Checking Bedroom 2 (JKL012)
🔥 Bedroom 2 has 1 critical issue(s)!
  • Cool Setpoint: 68.0°F → 74.3°F

(5/5) Checking Master Suite (MNO345)
✅ Master Suite is in sync!

Summary:
  • Total devices checked: 5
  • Devices in sync: 3
  • Devices with critical issues: 1
  • Devices with minor issues: 1

🔥 1 device(s) need immediate attention!
```

### Fixing Issues

```bash
$ uvx hvac-stability fix-device-settings "Bedroom 2"
Processing Bedroom 2 (JKL012)

Changes needed for Bedroom 2:
  • Cool Setpoint: 68.0°F → 74.3°F

Applying 1 change(s) to Bedroom 2...
✅ Applied 1 change(s) to Bedroom 2

Summary:
  • Total devices processed: 1
  • Devices fixed: 1
  • Devices already in sync: 0

✅ Successfully processed 1 device(s)!
```

### Detailed Single Device Check

```bash
$ uvx hvac-stability check-device-settings "Bedroom 2"
Checking Settings for Bedroom 2 (JKL012)

                   Settings Comparison
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Setting         ┃   Current    ┃   Expected   ┃ Status ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━┩
│ Heat Setpoint   │    68.0°F    │    68.0°F    │ ✅ OK  │
│ Cool Setpoint   │    74.3°F    │    74.3°F    │ ✅ OK  │
│ Mode            │   autoHeat   │     auto     │ ✅ OK  │
│ Fan Speed       │     auto     │     auto     │ ✅ OK  │
│ Vane Direction  │     auto     │     auto     │ ✅ OK  │
└─────────────────┴──────────────┴──────────────┴────────┘

Checked at: 2025-08-03 12:33:12
✅ Bedroom 2 is in sync!
```

## Commands

### Core Commands

- `login` - Authenticate with the Kumo cloud service
- `list` - Display all discovered devices
- `check-device-settings` - Check if devices match their schedules
- `fix-device-settings` - Automatically fix out-of-sync devices

### Device Management

- `store-device-ip` - Store local IP addresses for faster communication
- `show-schedule` - Display device scheduling information

## Configuration

The tool uses environment variables and local configuration files:

- **Credentials**: Stored securely in `~/.local/var/hvac_stability/.credentials`
- **Device Data**: Cached in `~/.local/var/hvac_stability/devices.json`
- **Temperature Unit**: Set via `KUMO_TEMPERATURE_UNIT` (F or C)

### Environment Variables

```bash
export KUMO_AUTH_USERNAME="your_username"
export KUMO_AUTH_PASSWORD="your_password"
export KUMO_TEMPERATURE_UNIT="F"  # or "C"
export KUMO_DATA_PATH="~/.local/var/hvac_stability/"
```

## Issue Types

The tool categorizes issues into two types:

### 🔥 Critical Issues
- **Temperature setpoints** that don't match the schedule
- These require immediate attention and can affect comfort/efficiency

### ⚠️ Minor Issues  
- **Mode variations** (auto/autoCool/autoHeat differences)
- **Fan speed** or **vane direction** discrepancies
- These are typically normal HVAC behavior and less concerning

## Exit Codes

When using `--exit-code` flag:
- `0` - All devices in sync or only minor issues
- `1` - Critical issues found or errors occurred

This makes the tool suitable for automation and monitoring scripts.

## Development

### Project Structure

- `src/hvac_stability/cli.py` - Main CLI application
- Uses `typer` for command-line interface
- Uses `pykumo` for Kumo cloud API integration
- Uses `rich` for enhanced terminal output

### Building and Testing

```bash
# Install development dependencies
uv sync

# Check code syntax
uv run python -m py_compile src/hvac_stability/cli.py

# Run the application
uv run hvac-stability --help
```

## Troubleshooting

### Authentication Issues
- Verify credentials with `hvac-stability login`
- Check that your Kumo account has access to the devices

### Device Communication
- Use `store-device-ip` to cache local IP addresses for faster access
- Some devices may require schedule setup in the Kumo app first

### Schedule Issues
- Ensure devices have active schedules configured
- Check that schedule events are set to "active" and "in use"

## License

This project is for personal HVAC management and monitoring.
