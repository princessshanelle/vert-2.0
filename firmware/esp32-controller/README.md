# esp32-controller: one ESP32 for the climate, soil, pH, pump and grow light

One **ESP32 DevKit V1 (30-pin, DOIT)** replaces the old standalone `VERT_ESP32.ino`, now working with
the VERT stack on the Pi. The board shows up in the dashboard as **five devices**, each with its own
login, readings and charts:

| Device | Type | What it sends or does |
|---|---|---|
| `climate-01` | temp-humidity | `temp_c`, `humidity_pct` every 5 s (DHT22) |
| `soil-01` | soil-moisture | `soil_moisture_pct` (the average of the probes) plus `soil_1_pct`…`soil_4_pct` and their raw values, every 10 s |
| `ph-01` | ph | `ph` and `ph_mv` every 10 s |
| `pump-01` | pump | `pump_on`, `pump_run_s`, `pump_backup`; commands `on` and `off` |
| `light-01` | light | `light_on`; commands `on` and `off` |

It works like the other VERT nodes: MQTT to the Pi, one session per device (the broker only lets a
device use its own topics), an announcement on connect, an "offline" last-will message when power is
lost, and automatic Wi-Fi and MQTT reconnects.

## Safety built into the board

- **The relays start OFF.** A reboot or power cut never starts the pump or the light.
- **The pump always stops after `PUMP_MAX_RUN_S`** (180 s by default), whatever turned it on: you, a
  rule, or the backup. This check runs on the ESP32 itself, so it works even with no Wi-Fi or Pi.
- **Backup watering:** if the board can't reach the Pi for `BACKUP_AFTER_S` (10 min), it waters by
  itself like the old sketch: pump on below 30% soil moisture, off at 70% or at the run limit, with at
  least `PUMP_MIN_REST_S` (15 min) between runs. If no soil probe reads sensibly, it never waters on its
  own. When the Pi is back, a backup run stops and your automation rules take over again.

## Wiring

| Part | ESP32 pin | Notes |
|---|---|---|
| DHT22 data | GPIO 4 | 10 kΩ pull-up to 3V3 if your DHT22 isn't on a breakout board |
| Soil probes 1–4 | GPIO 32, 33, 34, 35 | capacitive probes, powered from **3V3** |
| pH module output (Po) | **GPIO 36 (VP)** | see the warning below |
| Pump relay IN | GPIO 27 | relay powered from 5V; the pump runs from its own 12 V supply through the relay |
| Grow-light relay IN | GPIO 15 | the bulb's mains goes through the relay contacts only |

**Changed from the old sketch: the pH probe moves to GPIO 36 (VP).** The old sketch read pH on GPIO 25
and GPIO 12. Those are *ADC2* pins, and an ESP32 can't read ADC2 while Wi-Fi is on, so they return
nothing; no amount of re-sampling fixes that. GPIO 12 also decides the flash voltage at boot, so a
voltage on it at power-up can stop the board from booting. All analog sensors must use ADC1 pins: 32,
33, 34, 35, 36 (VP) and 39 (VN). If your board has no VP/VN pins, use 3 soil probes and put pH on 35
(see `config.h`).

> **Warning: pH module voltage.** Many pH boards (PH-4502C and similar) are powered from 5 V and can
> output **up to 5 V**. That damages an ESP32 input. Measure Po with a multimeter in pH 4 and pH 10
> buffer. If it ever goes above 3.3 V, add a divider (for example 10 kΩ from Po to GPIO 36, then 20 kΩ
> from GPIO 36 to GND) and calibrate with the divider fitted.

Connect all grounds together: the ESP32, the relays, and the pump supply's negative. **Mains wiring for
the bulb must be done safely**: an enclosed relay module, with live wires never exposed.

## Setup

1. **Provision the five devices.** In the dashboard, as admin, go to **Add device** and add each one with
   these **exact types**: `temp-humidity`, `soil-moisture`, `ph`, `pump`, `light`. Copy each secret as it
   appears; it's shown only once. Then, on each device's page, put all five in your greenhouse's zone.
2. **Configure.** Copy `src/config.h.example` to `src/config.h`, then fill in your Wi-Fi (2.4 GHz), the
   Pi's IP (`MQTT_HOST`), the zone name and the five IDs and secrets.
3. **Flash** with [PlatformIO](https://platformio.org) (the VS Code extension, or the command line):
   ```bash
   cd firmware/esp32-controller
   pio run -t upload && pio device monitor
   ```
   The serial monitor should show `Wi-Fi connected`, then `[pump-01] connected` and so on for all five,
   and a `soil raw: … | pH … mV` line every 10 s. `MQTT connect failed rc=5` means that device's ID or
   secret is wrong.
4. In the dashboard, all five devices come online in their zone. The Overview's climate and soil panels
   fill in, and the pump and light pages get **Turn on / Turn off** buttons.

## Calibration

The serial monitor prints the raw values every 10 s.

- **Soil:** hold a probe in dry air and note its raw value, then put it in a cup of water and note that.
  Set `SOIL_RAW_DRY` and `SOIL_RAW_WET` to those numbers. One pair is used for all probes; capacitive
  probes from the same batch are usually close.
- **pH:** rinse the probe, put it in pH 7.0 buffer and note the mV (call it A). Rinse again, put it in
  pH 4.0 buffer and note that (B). Set `PH_NEUTRAL_MV = A` and `PH_MV_PER_UNIT = (B - A) / 3`.

Re-flash after changing them. The dashboard charts `soil_n_raw` and `ph_mv` too, so you can also read
the values there.

## Automation rules (in the dashboard)

A rule fires once each time its condition becomes true, so watering needs two rules:

| Name | Device / metric | Condition | Action |
|---|---|---|---|
| Water when dry | `soil-01` / `soil_moisture_pct` | `<` 30 for 0 min | `pump-01`: `on` |
| Stop when wet | `soil-01` / `soil_moisture_pct` | `>=` 60 for 0 min | `pump-01`: `off` |

The board's run limit (`PUMP_MAX_RUN_S`) still applies. If one run isn't enough to bring the soil above
30%, the "Water when dry" rule won't fire again until the moisture has risen above 30% and dropped back.
So set `PUMP_MAX_RUN_S` to a normal watering for your setup.

For the grow light: switch it from its device page, or make a rule on a light reading, e.g. the plant camera's `lux` `<` 2000 → `light-01`: `on` (and a second rule `lux` `>` 8000 → `off`).

## What was dropped from the old sketch

- The board's own web page and settings: the VERT dashboard replaces them.
- The door motor with its limit switches (not wired for now). It can come back as a `window` device.
