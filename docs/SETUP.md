# Setup guide

Getting a MOCREO hub talking to Home Assistant takes about 25 minutes. There
are four parts: an MQTT broker, the MQTT integration, the hub's own MQTT
output, and this integration.

If you already run an MQTT broker, skip to [Step 4](#step-4--point-the-hub-at-your-broker).

---

## Step 1 — Install an MQTT broker

Home Assistant does not include a broker; you add one. On Home Assistant OS or
Supervised, the official add-on is the easiest route:

1. **Settings → Add-ons → Add-on Store**
2. Search **Mosquitto broker** (official, by Home Assistant)
3. **Install**, then enable **Start on boot** and **Watchdog**
4. **Start**

The defaults are fine. It listens on port `1883`.

On Container or Core installs, run Mosquitto (or any MQTT 3.1.1 broker)
alongside Home Assistant and note its address.

## Step 2 — Create a login for the hub

The Mosquitto add-on authenticates against Home Assistant user accounts. Make a
dedicated one rather than reusing your own.

1. **Settings → People → Users → Add User**
2. Name and username: `mqtt`; password: something long
3. Enable **Can only log in from the local network**
4. Leave **Administrator** off

## Step 3 — Add the MQTT integration

1. **Settings → Devices & Services → Add Integration → MQTT**
2. Broker: `core-mosquitto` (or your broker's address) · Port: `1883`
3. Username / password from Step 2
4. Submit

## Step 4 — Point the hub at your broker

You need the hub's LAN IP and your broker's LAN IP.

- Hub IP: MOCREO app → hub → device info, or your router's client list
- Broker IP: **Settings → System → Network** if you used the add-on

Then:

1. Open `http://<hub-ip>/` in a browser on the same network
2. Set **Hub Operation Mode** to **Hybrid Mode** — this keeps the MOCREO app
   working *and* enables MQTT output. Do not pick a cloud-only mode.
3. Fill in the MQTT settings:
   - **Server address**: your broker's IP (e.g. `192.168.1.50`)
   - **Port**: `1883`
   - **Username** / **Password**: the account from Step 2
4. Save

The hub uses its serial number as the MQTT client ID automatically — there is no
client ID field to fill in.

**No MQTT section in the portal?** Update the hub firmware from the MOCREO app,
then reload the page.

> Use an IP address, not a `.local` hostname. That relies on mDNS, which plenty
> of IoT firmware cannot resolve. Give your broker a DHCP reservation on your
> router so the address does not change later — the hub connects outward to the
> broker, so *its* address changing is harmless, but the broker's is not.

## Step 5 — Install this integration

**HACS** — HACS → three-dot menu → **Custom repositories** → add this repo's URL
with category **Integration** → find **MOCREO** → **Download** → restart Home
Assistant.

**Manual** — copy `custom_components/mocreo/` into your Home Assistant
`config/custom_components/` directory and restart.

Then: **Settings → Devices & Services → Add Integration → MOCREO**. Leave the
topic prefix as `mocreo` and the temperature option on.

## Step 6 — Wait for the sensors to appear

Entities are created as readings arrive. LoRa nodes are battery-powered and
report on their own schedule, so allow up to 30 minutes before troubleshooting.

To force a report: change the sensor's environment — pull a temperature probe
out of the fridge for a minute, or briefly wet a leak sensor's contacts.

Each node becomes a device named like `MOCREO LS2 000100`. Rename these to
something meaningful before writing automations, since entity IDs follow the
device name.

> Leak sensors expose a **Water leak** binary sensor (Wet / Dry) — that is the
> one to automate against. They also report an onboard **temperature**, which is
> genuinely useful for freeze warnings on pipes, and a diagnostic **Water level**
> number that the binary sensor is derived from.

---

## Automation examples

Adjust the entity IDs to match your own devices.

### Water leak

```yaml
alias: Water leak detected
mode: parallel
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.leak_sensor_1_water_leak
      - binary_sensor.leak_sensor_2_water_leak
    to: "on"
actions:
  - action: notify.notify
    data:
      title: "💧 Water leak"
      message: >-
        {{ trigger.to_state.attributes.friendly_name }} is wet —
        {{ state_attr(trigger.entity_id, 'level_label') }}
        ({{ now().strftime('%-I:%M %p') }})
      data:
        priority: high
        ttl: 0
        channel: alarm_stream
```

### Water rising — a different emergency

Leak sensors report a depth step, not just wet/dry. A damp patch under a sink
and water climbing the side of a basement sensor deserve different responses.

```yaml
alias: Water rising
mode: parallel
triggers:
  - trigger: numeric_state
    entity_id:
      - sensor.leak_sensor_1_water_level
      - sensor.leak_sensor_2_water_level
    above: 1
actions:
  - action: notify.notify
    data:
      title: "🌊 Water RISING"
      message: >-
        {{ trigger.to_state.attributes.friendly_name }} —
        {{ state_attr(trigger.entity_id, 'level_label') }}
        (level {{ trigger.to_state.state }} of
        {{ state_attr(trigger.entity_id, 'level_scale_max') }}).
        This is more than a drip.
      data:
        priority: high
        ttl: 0
        channel: alarm_stream
```

The **Water level** entity is a diagnostic one, so it lives further down the
device page — it still works in automations exactly like any other sensor.

### Freezer too warm — but only if it stays warm

A single spike when someone opens the door is not a failure. Waiting 20 minutes
means you are only alerted for a real problem.

```yaml
alias: Freezer too warm
mode: single
triggers:
  - trigger: numeric_state
    entity_id: sensor.freezer_temperature
    above: -12
    for: "00:20:00"
actions:
  - action: notify.notify
    data:
      title: "🌡️ Freezer warming up"
      message: >-
        Freezer has been at {{ states('sensor.freezer_temperature') }}°C
        for 20 minutes.
      data:
        priority: high
        ttl: 0
        channel: alarm_stream
```

### Sensor went quiet

A dead battery or a hub that dropped off looks exactly like "everything is
fine." This is the failure mode people forget to alert on.

```yaml
alias: MOCREO sensor offline
mode: parallel
triggers:
  - trigger: state
    entity_id:
      - binary_sensor.freezer_connectivity
      - binary_sensor.leak_sensor_1_connectivity
    to: "off"
    for: "02:00:00"
actions:
  - action: notify.notify
    data:
      title: "MOCREO sensor offline"
      message: >-
        {{ trigger.to_state.attributes.friendly_name }} has not reported
        in 2 hours.
```

### Low battery

```yaml
alias: MOCREO low battery
mode: single
triggers:
  - trigger: numeric_state
    entity_id:
      - sensor.freezer_battery
      - sensor.leak_sensor_1_battery
    below: 20
actions:
  - action: persistent_notification.create
    data:
      title: "MOCREO battery low"
      message: >-
        {{ trigger.to_state.attributes.friendly_name }} is at
        {{ trigger.to_state.state }}%.
```

---

## Troubleshooting

**Nothing appears after 30+ minutes.**
Check whether the hub is publishing at all: **Settings → Devices & Services →
MQTT → Configure → Listen to a topic**, enter `mocreo/#`, press Start, then
trigger a sensor. If nothing arrives, the hub is not reaching the broker —
recheck the address, port and credentials in the hub portal.

**Temperatures are 100× too small** (e.g. `-0.18` instead of `-18`).
MOCREO's documentation says the `data` topic sends hundredths of a degree, and
this integration divides integer values by 100. If a firmware update changes
that, open the MOCREO integration → **Configure** and turn off *Hub sends
temperature in hundredths of a degree*.

**You want °F rather than °C.**
Leave the integration alone. Click the entity, open its settings, and set the
display unit — Home Assistant converts per-entity and the stored data stays
consistent.

**A sensor produced an oddly-named entity.**
MOCREO has not published field names for every model, so unrecognised JSON keys
fall back to generic entities. Open the device page → three-dot menu →
**Download diagnostics**; it includes the last raw payload, which is all that is
needed to add a proper mapping. Please open an issue with it attached — but
scrub your hub serial number and node IDs first if you would rather not publish
them.

**"MQTT integration is not set up yet" when adding MOCREO.**
Steps 1–3 were skipped, or the broker is not running.

---

## Why MQTT rather than the alternatives

- **BLE / Passive BLE Monitor** — MOCREO's own Home Assistant guide uses this,
  but it only reaches their Bluetooth sensors within radio range of the Home
  Assistant machine. For LoRa models that defeats the point.
- **The MOCREO cloud API** (`api.mocreo.com`) — works, but it is polled,
  internet-dependent, and routes your sensor data through a third party.
- **Waiting for the official integration** — MOCREO lists one as "under
  development" with no published date.

MQTT is local, push-based, requires no account, and is documented by MOCREO
rather than reverse-engineered.
