# Litmus NVR for Home Assistant

Custom integration that connects Home Assistant to a [Litmus NVR](https://litmusnvr.com) device on your local network.

## What you get

For each camera:

- **Camera** with live view over WebRTC (Home Assistant's native WebRTC player) and snapshots
- **Binary sensors**: person, vehicle, animal and sound detected (cleared 30 seconds after the last detection)
- **Sensors**: number of people/vehicles/animals, last recognized face, last license plate
- **Event entity** that fires for each Litmus alert, with the alert title, message and a signed snapshot URL
- **Image entity** with the latest alert snapshot

Plus an **update** entity for the Litmus software and a blueprint that forwards Litmus alerts to the Home Assistant mobile app.

Updates are pushed from the device over a local WebSocket (`local_push`); nothing goes through the cloud.

## Requirements

- Litmus NVR 2.4.0 or later, with **Settings › Integrations › Home Assistant** turned on
- Home Assistant 2025.1 or later
- Home Assistant and the Litmus device on the same network (the device listens on port 8128)

## Install

### HACS

1. In HACS, open the menu › **Custom repositories**.
2. Add `https://github.com/grablo/litmus-hass-integration` with type **Integration**.
3. Install **Litmus NVR** and restart Home Assistant.

### Manual

Copy `custom_components/litmus_nvr` into the `custom_components` folder of your Home Assistant configuration and restart.

## Set up

1. **Settings › Devices & services › Add integration › Litmus NVR**.
2. Enter the address shown in Litmus under **Settings › Integrations** (for example `192.168.0.10:8128`).
3. Sign in with the Litmus local account. Home Assistant keeps a device token; your password is not stored.

To disconnect, remove the integration in Home Assistant or press **Disconnect** next to it in Litmus.

## Alert notifications

Import `blueprints/automation/litmus_nvr/notify.yaml` (**Settings › Automations › Blueprints › Import**) to send each Litmus alert to a phone with its snapshot.
