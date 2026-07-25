# MOCREO for Home Assistant

A custom integration that brings MOCREO LoRa/BLE sensors into Home Assistant as
native entities, using the **local MQTT output** built into MOCREO Smart Hubs
(H3 / H5 / H5 Pro / H6). No MOCREO cloud account, no polling, no subscription.

Developed and tested against an **H5 Pro** with **LS2** temperature and **LW1**
water leak sensors, but written to be model-agnostic — see
[Unknown models](#unknown-models-and-new-fields) below.

## What you get

Every sensor paired to the hub becomes its own Home Assistant device, hanging
off a parent "MOCREO Hub" device, with entities created automatically as
readings arrive:

| Reading | Entity | Notes |
|---|---|---|
| Temperature | `sensor` | device class `temperature`, °C, long-term statistics |
| Humidity / pressure | `sensor` | when the model reports them |
| Water leak | `binary_sensor` | device class `moisture` |
| Leak severity / level | `sensor` | plus a numeric `severity_rank` attribute for automations |
| Rule trigger | `binary_sensor` | device class `problem`, follows the hub's own threshold rules |
| Battery | `sensor` | diagnostic |
| Signal strength | `sensor` | diagnostic, dBm |
| Probe connected | `binary_sensor` | diagnostic, connectivity |
| Node online | `binary_sensor` | diagnostic, connectivity |

### Unknown models and new fields

MOCREO has not published field names for every sensor, so unrecognised JSON keys
are turned into sensors (numbers and strings) or binary sensors (booleans)
automatically, using whatever unit the hub declares in its `config` response.
Nothing is silently dropped, and a model this integration has never seen still
produces usable entities.

## Requirements

- Home Assistant 2024.6 or newer (developed against 2026.7)
- An MQTT broker reachable from the hub — the **Mosquitto broker** add-on is fine
- The MQTT integration configured in Home Assistant
- A MOCREO hub running firmware that exposes MQTT in its LAN web portal

## Install

**[Full setup guide →](docs/SETUP.md)** — broker, hub portal, integration, and
automation examples. The short version:

**HACS (custom repository)** — add this repo as an Integration, download,
restart.

**Manual** — copy `custom_components/mocreo/` into your Home Assistant
`config/custom_components/` folder and restart.

Then: *Settings → Devices & Services → Add Integration → MOCREO*. The only
questions are the MQTT topic prefix (leave it as `mocreo`) and whether the hub
sends temperature in hundredths of a degree (leave it on; turn it off only if
temperatures read 100× too small).

## Hub setup

1. Find the hub's IP on your LAN and open `http://<hub-ip>/`.
2. Set **Hub Operation Mode** to **Hybrid Mode**.
3. Enter your broker's address, port `1883`, and username/password if you set one.
4. Save. The hub uses its serial number as the MQTT client ID automatically.

If there is no MQTT section in the portal, update the hub firmware from the
MOCREO app first.

## Topics used

```
mocreo/<hub_sn>/node/<node_id>/data        measurements        (subscribed)
mocreo/<hub_sn>/node/<node_id>/event       rule trigger/recover (subscribed)
mocreo/<hub_sn>/node/<node_id>/state       online/battery/signal (subscribed)
mocreo/<hub_sn>/node/<node_id>/config      model + units        (subscribed)
mocreo/<hub_sn>/node/<node_id>/config/get  model/unit request   (published)
```

## Surviving restarts

LoRa nodes report infrequently, so the integration caches which entities it has
discovered in the config entry and restores their last values on restart.
Entities come back immediately instead of disappearing until the next
transmission.

## Reporting a sensor that looks wrong

Open the device page → three-dot menu → **Download diagnostics**. The file
contains the last raw MQTT payload seen for every node, which is all anyone
needs to add a proper mapping for a new model.

## Testing

`python3 tests/test_parser.py` runs the payload-parsing test suite standalone —
no Home Assistant install required.

## License

MIT.
