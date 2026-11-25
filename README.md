# Litmus NVR for Home Assistant

Custom integration that connects Home Assistant to a [Litmus NVR](https://litmusnvr.com) device on your local network.

Don't have Litmus NVR yet? Every feature is free, with no camera limit. Install it as a Home Assistant app:

[![Open your Home Assistant instance and show the add app repository dialog with the Litmus NVR repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fgrablo%2Flitmus-hass-addons)

Or install it on a Windows, Mac or Linux computer or a Raspberry Pi from [litmusnvr.com/download](https://litmusnvr.com/download).

## What you get

For each camera:

- **Camera** with live view over WebRTC (Home Assistant's native WebRTC player) and snapshots. On Home Assistant 2026.11 or later, cameras that support two-way audio get a talk button (turn on the **Two-way audio** preview in **Settings › System › Labs**)
- **Binary sensors**: person, vehicle, animal and sound detected (cleared 30 seconds after the last detection), plus "any object"
- **Sensors**: number of people/vehicles/animals, last recognized face, last license plate
- **Detection zones**: each zone you draw in Litmus becomes a device with its own person/vehicle/animal sensors and counts
- **Event entity** that fires for each Litmus alert, with the alert title, message, a signed snapshot URL and a clip URL
- **Image entity** with the latest alert snapshot
- **Switches**: turn detection or recording off and on (back on when Litmus restarts)

For the Litmus device:

- **Media panel**: browse Litmus cards (people, faces, vehicles, animals) and each camera's recordings by day, and play them in Home Assistant
- **Sidebar** link that opens the Litmus web interface
- **Phone notifications** switch for Litmus's own push notifications
- **Update** entity for the Litmus software
- **Diagnostic sensors** (status, uptime, CPU and memory usage, inference speed, camera and detection fps), off by default

Plus a blueprint that forwards Litmus alerts to the Home Assistant mobile app.

Updates are pushed from the device over a local WebSocket (`local_push`); nothing goes through the cloud.

## Requirements

- Litmus NVR 2.4.0 or later, with **Settings › Integrations › Home Assistant** turned on
- Home Assistant 2025.1 or later
- Home Assistant and the Litmus device on the same network (the device listens on port 8128)

## Install

### Marketplace (Home Assistant 2026.11 or later)

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=grablo&repository=litmus-hass-integration&category=integration)

Or go to **Settings › Marketplace**, select **Add from link**, enter `https://github.com/grablo/litmus-hass-integration` under **GitHub link** and select **Add**. Install **Litmus NVR**, and restart Home Assistant if you are asked to.

### HACS (Home Assistant 2026.10 or earlier)

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
