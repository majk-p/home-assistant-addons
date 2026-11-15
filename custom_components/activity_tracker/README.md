# Activity Tracker — native Home Assistant integration

A custom integration version of the [YAML-package baby activity
tracker](../README.md): feedings, pees, poo (small) and poo (large), one
config entry per child. You add a child in the UI, press buttons to log
events, and get counters, daily bar charts, a timeline and feeding-overdue
notifications. Everything is local — no MQTT, no add-on, no external
services; state lives in a Home Assistant storage file and the local
recorder/long-term statistics.

## Distributing from your own GitHub repo (add-ons repo or standalone)

This is an integration, not an add-on — it cannot be installed through the
Add-on Store (add-ons are Docker containers and live outside HA). Since HA
2026.11, HACS is part of Home Assistant core as the **Marketplace**
(Settings → Marketplace), using the same catalog and the same `hacs.json`
format. A repository can serve add-ons AND integrations at the same time —
the add-on store only lists directories with an add-on `config.yaml`, the
Marketplace only looks under `custom_components/`.

Layout to add to e.g. the `majk-p/home-assistant-addons` repo root:

```
repository.json                  (already there for the add-on store)
reverse-ssh-tunnel/              (existing add-on)
custom_components/
  activity_tracker/              (copy of this folder, verbatim)
hacs.json
```

`hacs.json` (repo root):

```json
{
  "name": "Activity Tracker",
  "render_readme": true
}
```

To install from that repo on HA 2026.11+: **Settings → Marketplace →
Add from link** → the repository URL. Adding a custom repository requires a
GitHub connection (device sign-in in the dialog; it only reads public
repositories — yours is public). The integration then appears in the
Marketplace; **Install** drops you straight into its setup flow, no restart.
Updates: bump `manifest.json`'s `version`, push a Git tag / GitHub release
with the same version, and the update shows up under Settings → Updates.

Note: the Marketplace runs a few validation checks before installing; see
the [validation reference](https://contributors.home-assistant.io/marketplace/reference/validation/)
if it ever refuses. Blueprints, AppDaemon apps and Python scripts are not
Marketplace-installable (irrelevant here, but good to know).


## Install



1. Copy `activity_tracker/` into `/config/custom_components/` (create the
   directory if needed), so you have
   `/config/custom_components/activity_tracker/manifest.json`.
2. Restart Home Assistant completely (Settings → System → power icon →
   Restart).
3. Settings → Devices & services → Add Integration → search for
   **Activity Tracker** → fill in the child:
   - **Name** — display name, any text (e.g. `Robert`); used in entity names,
     the device and notifications.
   - **Entity prefix** — optional; defaults to the name's slug (`rob`).
     Lowercase letters, digits, underscores. All entity ids are built from
     it (`button.rob_feeding`, ...).
   - **Feeding interval (hours)** — target interval, 1–6, step 0.25,
     default 3. Editable later.
   - **Notify targets** — optional, comma-separated notify entities
     (`notify.mobile_app_dad, notify.mobile_app_mom`) for overdue-feeding
     notifications.
4. The entry appears as "Robert (rob)" with one device per child holding
   26 entities:

   | Entities | Meaning |
   | --- | --- |
   | `button.<p>_<activity>` / `button.<p>_<activity>_undo` | Press = event happened now / undo last event |
   | `sensor.<p>_<activity>_total` | Lifetime count, `state_class: total_increasing`, never reset; its long-term statistics drive the daily charts |
   | `sensor.<p>_<activity>_today` | Count since local midnight (resets at local midnight, DST-safe) |
   | `sensor.<p>_<activity>_last` | Timestamp of the last event |
   | `sensor.<p>_<activity>_daily_avg` | Total per completed day since the first event ever (rounds to 1 decimal) |
   | `number.<p>_feeding_interval_hours` | Feeding interval in hours (box, 1–6, step 0.25) |
   | `binary_sensor.<p>_feeding_due` | ON once `now ≥ last feeding + interval + 10 min grace` |

   Activities are `feeding`, `pee`, `poo_small`, `poo_large`. All state
   (totals, today counts, first/last timestamps, interval, notification
   bookkeeping) is persisted per child in `.storage/activity_tracker_<p>`
   and survives restarts.

## Usage

- Press the dashboard tiles (see `dashboard.example.yaml` — find & replace
  `rob`/`Robert`) or call `button.press` on any event button. Undo buttons
  decrement the counters (floored at 0).
- Everything that works with native buttons works here: dashboards,
  scripts, automations, voice assistants.
- Feeding interval: edit `number.<p>_feeding_interval_hours` on the
  dashboard or in the entry's options — both stay in sync (the entity is
  the source of truth once created).
- Feeding reminders: when `binary_sensor.<p>_feeding_due` turns ON, and at
  most once per hour while it stays ON, each configured notify target gets
  a `notify.send_message` call: "Robert feeding overdue — last fed 3h 20m
  ago (interval 3h)" (in Polish: "Robert: karmienie po terminie — ostatnio
  3h 20m temu (odstęp 3h)"; the message follows the HA profile language,
  see Localization). Targets that no longer exist are skipped (logged as
  a warning). Until the first feeding is logged there is nothing to be
  overdue against, so the sensor stays OFF.

### Wiring Zigbee buttons

Physical buttons now call the standard `button.press` action directly —
no scripts needed. ZHA example (single press = small poo, double press =
large poo; get the `device_id` from a UI-created Device trigger):

```yaml
automation:
  - id: rob_zha_poo_button
    alias: rob ZHA poo button
    description: Single press logs a small poo, double press a large poo.
    triggers:
      - trigger: device
        domain: zha
        device_id: REPLACE_WITH_YOUR_DEVICE_ID
        type: remote_button_short_press
        subtype: remote_button_short_press
        id: single
      - trigger: device
        domain: zha
        device_id: REPLACE_WITH_YOUR_DEVICE_ID
        type: remote_button_double_press
        subtype: remote_button_double_press
        id: double
    actions:
      - choose:
          - conditions: "{{ trigger.id == 'single' }}"
            sequence:
              - action: button.press
                target:
                  entity_id: button.rob_poo_small
          - conditions: "{{ trigger.id == 'double' }}"
            sequence:
              - action: button.press
                target:
                  entity_id: button.rob_poo_large
```

For Zigbee2MQTT, trigger on the button's `action` and call the same
`button.press`.

## Multi-kid

Settings → Devices & services → Activity Tracker → Add Integration, add
another child with a **different prefix** (enforced). Entities, devices
and storage files are fully separated. Paste a second copy of the
dashboard example with the other prefix.

## Options

Configure on the entry (or "Edit" in the integration page) to change name,
prefix, feeding interval and notify targets. Changing name/prefix/targets
reloads the entry. Changing the prefix changes **all entity ids** of that
child — long-term statistics follow entity ids, so rename only when
needed; old entities are cleaned up automatically.

## Localization

Entity names, the config/options dialogs and the overdue-feeding
notification follow the Home Assistant profile language automatically
(Settings → People → your user → Language). English and Polish ship in
`activity_tracker/translations/` (`en.json` is generated from
`strings.json`, the canonical source).

- **Entity ids never change with language.** They are pinned to the
  prefix you chose (`button.rob_feeding` stays `button.rob_feeding`);
  only the friendly names localize ("Robert feeding" / "Robert karmienie").
  Dashboards, automations and long-term statistics keep working when you
  switch languages.
- To add another language: copy `translations/en.json` to
  `translations/<language code>.json` (e.g. `de.json`), translate the
  values — keep the `{child_name}` and `{…}` placeholders as they are —
  and open a pull request.
- The Polish baby-wording ("kupa (mała)", "siku", "karmienie po
  terminie", …) is a **draft awaiting native-speaker review**; comment on
  the repository if something sounds off.

## Migration from the YAML package

1. **Remove the YAML package first**: delete the generated package file
   from `/config/packages/` and restart Home Assistant. Do this *before*
   adding the child entry — the integration's `sensor.<prefix>_<activity>_total`
   entities reuse the same entity ids, and they only get them if the YAML
   template sensors are gone. Long-term statistics (and therefore your
   charts' history) continue seamlessly.
2. Add the child entry with the **same prefix** as the package used
   (`rob` in the examples).
3. Rewire any button automations from the generated scripts
   (`script.rob_log_poo_small`) to `button.press` on the new entities
   (see the ZHA example above).
4. Replace the dashboard view with `dashboard.example.yaml` (same layout,
   new entity domains). The four `statistics-graph` cards reference the
   identical `sensor.<p>_<activity>_total` ids, so charts keep their
   history.

Migration caveats: the new `*_total` sensor *values* start from 0 (the
per-day chart history remains via long-term statistics), and the
first-event marker for Avg/day starts at the first press with the
integration — averages begin anew.

## Known artifacts

- **Undo dip in statistics**: `*_total` is `total_increasing`, so an undo
  (a decrease) is treated as a meter reset by long-term statistics. If
  the undo lands in a different statistics period than the press, that
  day's bar can show a small blip. Cosmetic; today counters are always
  exact.
- **Undo does not move the `*_last` timestamp**: an undo removes a count,
  not history. `sensor.<p>_<activity>_last` keeps the time of the last
  press (and feeding-due is unaffected by an undo).
- **Feeding due before the first event**: with no feeding logged yet the
  binary sensor stays OFF; press the feeding button once to start.
- **Restart while overdue**: the state is restored and the reminder fires
  (at most once per hour), so a restart does not spam notifications.

## Development and tests

```
integration/
  activity_tracker/      the component (copy to /config/custom_components/)
  custom_components/     test-only package: makes the loader see the component
  tests/                 pytest suite (config flow, entities, feeding, logic,
                         localization)
  conftest.py            wires the pytest-homeassistant-custom-component harness
  dashboard.example.yaml dashboard example
```

Run the tests with the pinned harness:

```bash
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component pytest-asyncio
.venv/bin/python -m pytest -q          # from this directory

Note for sandboxed/restricted environments: if pip fails with
"No space left on device" while building wheels, /tmp is a small tmpfs —
create the venv on a large filesystem and point pip's temp there:
`TMPDIR=/var/tmp/pip-tmp .venv/bin/pip install ...`.
```

The harness (pytest-homeassistant-custom-component) pins an exact Home
Assistant release — this suite was written and verified against
0.13.205 / Home Assistant 2025.1.4 (the newest pin supporting the
available Python 3.12). The integration itself targets current HA
(2026.11): `voluptuous` schemas keep working there (HA aliases the module
to its replacement), and the entity-id / options-flow mechanics were
checked against the 2026.11 sources. `python3 -m py_compile` every
`.py` and `json.load` every JSON file as a final check.

The `documentation` / `issue_tracker` URLs in `manifest.json` point at
this repository; adjust them if you fork.

## Uninstall

Delete the child entry (Settings → Devices & services → Activity Tracker →
the child → Delete; this removes the device and the per-child storage
file), delete `/config/custom_components/activity_tracker/`, restart, and
remove the dashboard view.
