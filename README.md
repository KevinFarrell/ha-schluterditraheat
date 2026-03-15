# Schluter DITRA-HEAT

A custom [Home Assistant](https://www.home-assistant.io/) integration for [Schluter DITRA-HEAT](https://www.schluter.com/schluter-us/en_US/ditra-heat) WiFi floor heating thermostats that use the [schluterditraheat.com](https://schluterditraheat.com) cloud service.

## Compatibility

Schluter has multiple apps and cloud platforms for different product lines. This integration works with thermostats managed through the **Schluter Smart Thermostat** app and [schluterditraheat.com](https://schluterditraheat.com) — **not** the older Schluter DITRA-HEAT app or other Schluter platforms. If you can log in at [schluterditraheat.com](https://schluterditraheat.com) with your credentials, this integration should work for you.

Tested with the **DITRA-HEAT-E-RS1** thermostat. Other models using the same cloud service should work but have not been verified. If you encounter issues with a different model, please [open an issue](https://github.com/KevinFarrell/ha-schluterditraheat/issues).

## Features

- **Climate entity** — control temperature and mode (Auto, Heat/Manual, Off) per thermostat
- **Heating output sensor** — track heating output percentage with history graphs and long-term statistics
- **GFCI fault sensor** — binary sensor for ground fault detection, enabling safety automations

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu → **Custom repositories**
3. Add `https://github.com/KevinFarrell/ha-schluterditraheat` with category **Integration**
4. Search for "Schluter DITRA-HEAT" and install
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for "Schluter DITRA-HEAT"

### Manual

1. Copy the `custom_components/schluterditraheat/` directory to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings → Devices & Services → Add Integration** and search for "Schluter DITRA-HEAT"

## Configuration

Enter your [schluterditraheat.com](https://schluterditraheat.com) account credentials when prompted by the config flow. All thermostats on your account will be discovered automatically.

**Note:** Adding or removing thermostats from your Schluter account requires reloading the integration in Home Assistant.

## Entities

Each thermostat creates the following entities, grouped under a single device:

| Entity | Type | Description |
|--------|------|-------------|
| Floor Heat | Climate | Temperature control and mode selection |
| Heating Output | Sensor | Current heating output percentage (0–100%) |
| GFCI Status | Binary Sensor | Ground fault detection (problem device class) |

## Limitations

This integration supports monitoring and basic control. The following are **not** currently supported:

- Managing or editing heating schedules (schedules configured in the Schluter app are respected in Auto mode)
- Changing the air/floor sensor mode
- Firmware updates
- Adding or removing thermostats (requires reloading the integration)

## Disclaimer

This project is not affiliated with, endorsed by, or associated with Schluter Systems. It uses the existing schluterditraheat.com web APIs, which are undocumented and may change at any time. If the APIs change, this integration may break until it is updated.

## Requirements

- Home Assistant 2024.1 or later
- A Schluter DITRA-HEAT WiFi thermostat with a [schluterditraheat.com](https://schluterditraheat.com) account
