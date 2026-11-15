/**
 * Activity Tracker — self-registering dashboard strategy.
 *
 * Loaded automatically by the integration (registered as an extra frontend
 * module URL from Python), so no manual resource setup is needed: the file
 * defines the custom element Home Assistant looks up for
 * `strategy: type: custom:activity-tracker` dashboards.
 *
 * Contract (verified against home-assistant/frontend 20250102.0 and current
 * master, see integration README): a dashboard strategy is a custom element
 * named `ll-strategy-dashboard-<name>` with a static `generate(config, hass)`
 * returning a whole dashboard configuration. On 2026.5+ frontends this file
 * also registers itself in `window.customStrategies`, which makes
 * "Activity Tracker" appear under the community/custom heading of the
 * Add-dashboard dialog; on older frontends that array is simply ignored.
 *
 * The generated dashboard mirrors dashboard.example.yaml one section per
 * child: overview table, feeding status, button tiles, interval setting,
 * four daily statistics charts and a 24 h logbook timeline. Children are
 * discovered from `hass.states` by entity id, so every configured child is
 * included automatically and renames / new children are followed.
 */

const STRATEGY_NAME = "activity-tracker";
const ELEMENT_TAG = `ll-strategy-dashboard-${STRATEGY_NAME}`;

// Activities in display order (must mirror const.py ACTIVITIES).
const ACTIVITIES = ["feeding", "pee", "poo_small", "poo_large"];

// English labels of the table rows / tile names (v1 is English-only, like
// dashboard.example.yaml; see README "Localization").
const ACTIVITY_LABELS = {
  feeding: "Feeding",
  pee: "Pee",
  poo_small: "Poo (small)",
  poo_large: "Poo (large)",
};

const ACTIVITY_ICONS = {
  feeding: "mdi:baby-bottle-outline",
  pee: "mdi:water",
  poo_small: "mdi:emoticon-poo",
  poo_large: "mdi:emoticon-poo-outline",
};
const UNDO_ICON = "mdi:undo-variant";

// Chart entity names, as in dashboard.example.yaml.
const TOTAL_CHART_NAMES = {
  feeding: "Feedings",
  pee: "Pees",
  poo_small: "Poo (small)",
  poo_large: "Poo (large)",
};

// Which object-id suffixes the integration creates, per entity domain. Used
// to split `<prefix>_<suffix>` entity ids into (child prefix, suffix).
const DOMAIN_SUFFIXES = {
  button: new Set([
    ...ACTIVITIES,
    ...ACTIVITIES.map((a) => `${a}_undo`),
  ]),
  sensor: new Set(
    ACTIVITIES.flatMap((a) => [
      `${a}_total`,
      `${a}_today`,
      `${a}_last`,
      `${a}_daily_avg`,
    ])
  ),
  number: new Set(["feeding_interval_hours"]),
  binary_sensor: new Set(["feeding_due"]),
};

// Friendly names look like "<child> feeding" (English) / "<child>
// karmienie" (Polish) — strip the trailing activity word to get the child's
// display name. Add more words here if new translations end differently.
const NAME_LABELS = ["feeding", "karmienie"];

const DEFAULT_INTERVAL_HOURS = 3.0;

const PLACEHOLDER_CONTENT = [
  "## 👶 Activity Tracker",
  "",
  "No children configured yet — this dashboard fills itself once the",
  "integration has entities.",
  "",
  "1. **Settings → Devices & services → Add Integration**",
  "2. Search for **Activity Tracker** and add a child (name, prefix,",
  "   feeding interval).",
  "3. Reopen this dashboard — a section per child appears automatically,",
  "   with buttons, overview, charts and the feeding reminder.",
].join("\n");

// ---------------------------------------------------------------------------
// Time helpers (JS equivalents of the Jinja time_since / time_until filters
// used by dashboard.example.yaml: at most 2 units, biggest first).
// ---------------------------------------------------------------------------

function humanizeDuration(ms) {
  const total = Math.floor(Math.abs(ms) / 1000);
  const units = [
    ["d", Math.floor(total / 86400)],
    ["h", Math.floor((total % 86400) / 3600)],
    ["m", Math.floor((total % 3600) / 60)],
    ["s", total % 60],
  ];
  const parts = [];
  for (const [label, value] of units) {
    if (value > 0) {
      parts.push(`${value}${label}`);
    }
    if (parts.length === 2) {
      break;
    }
  }
  return parts.length ? parts.join(" ") : "0s";
}

const timeSince = (date, now) => humanizeDuration(now - date.getTime());
const timeUntil = (date, now) => humanizeDuration(date.getTime() - now);

function parseTimestamp(state) {
  // Timestamp sensors serialize to ISO strings with offset. Anything else
  // (unknown / unavailable / missing) counts as "never".
  if (!state || state === "unknown" || state === "unavailable") {
    return null;
  }
  const date = new Date(state);
  return Number.isNaN(date.getTime()) ? null : date;
}

function stateNumber(state, fallback = 0) {
  if (state == null || state === "unknown" || state === "unavailable") {
    return fallback;
  }
  const value = Number(state);
  return Number.isNaN(value) ? fallback : value;
}

const hhmm = (date) =>
  `${String(date.getHours()).padStart(2, "0")}:${String(
    date.getMinutes()
  ).padStart(2, "0")}`;

// ---------------------------------------------------------------------------
// Child discovery
// ---------------------------------------------------------------------------

function findChildren(states) {
  // Return { prefix: { entities: Set(entityId) } } for every child whose
  // integration entities are present, by splitting entity ids on the known
  // suffixes. Prefixes are derived only from actual entities, so renames
  // and new children are followed automatically.
  const children = new Map();
  for (const entityId of Object.keys(states || {})) {
    const dot = entityId.indexOf(".");
    const domain = entityId.slice(0, dot);
    const suffixes = DOMAIN_SUFFIXES[domain];
    if (!suffixes) {
      continue;
    }
    const object_id = entityId.slice(dot + 1);
    const parts = object_id.split("_");
    for (let i = 1; i < parts.length; i++) {
      const suffix = parts.slice(i).join("_");
      if (suffixes.has(suffix)) {
        const prefix = parts.slice(0, i).join("_");
        if (!prefix) {
          break;
        }
        if (!children.has(prefix)) {
          children.set(prefix, { entities: new Set() });
        }
        children.get(prefix).entities.add(entityId);
        break;
      }
    }
  }
  return children;
}

function childDisplayName(prefix, states, config) {
  // 1. Optional per-child config: children: { rob: { name: "Robert" } } or
  //    children: { rob: "Robert" }.
  const override = (config.children || {})[prefix];
  if (typeof override === "string") {
    return override;
  }
  if (override && typeof override.name === "string") {
    return override.name;
  }
  // 2. The feeding tile's friendly name is "<child> feeding" in every
  //    shipped translation; strip the trailing activity word.
  const friendly =
    states[`button.${prefix}_feeding`]?.attributes?.friendly_name;
  if (typeof friendly === "string") {
    for (const label of NAME_LABELS) {
      const pattern = new RegExp(`^(.+) ${label}$`, "i");
      const match = friendly.match(pattern);
      if (match && match[1].trim()) {
        return match[1].trim();
      }
    }
  }
  // 3. Give up: use the prefix.
  return prefix;
}

// ---------------------------------------------------------------------------
// Card builders (mirroring dashboard.example.yaml)
// ---------------------------------------------------------------------------

function overviewCard(name, prefix, states) {
  const lines = [
    "| Activity | Today | Avg/day | Last event |",
    "| --- | --- | --- | --- |",
  ];
  const now = new Date();
  for (const activity of ACTIVITIES) {
    const last = parseTimestamp(states[`sensor.${prefix}_${activity}_last`]?.state);
    lines.push(
      `| ${ACTIVITY_LABELS[activity]}` +
        ` | ${Math.trunc(stateNumber(states[`sensor.${prefix}_${activity}_today`]?.state))}` +
        ` | ${stateNumber(states[`sensor.${prefix}_${activity}_daily_avg`]?.state).toFixed(1)}` +
        ` | ${last === null ? "never" : `${timeSince(last, now)} ago`} |`
    );
  }
  const pooToday =
    Math.trunc(stateNumber(states[`sensor.${prefix}_poo_small_today`]?.state)) +
    Math.trunc(stateNumber(states[`sensor.${prefix}_poo_large_today`]?.state));
  const pooAvg =
    stateNumber(states[`sensor.${prefix}_poo_small_daily_avg`]?.state) +
    stateNumber(states[`sensor.${prefix}_poo_large_daily_avg`]?.state);
  lines.push(`| **Poo (total)** | ${pooToday} | ${pooAvg.toFixed(1)} | — |`);
  lines.push("");
  lines.push(
    "*Avg/day = lifetime total per completed day since the first",
    "logged event (computed by the integration; today counts as an",
    "incomplete day). Exact daily numbers: charts below.*"
  );
  return { type: "markdown", title: `${name} - overview`, content: lines.join("\n") };
}

function feedingStatusCard(name, prefix, states) {
  const last = parseTimestamp(states[`sensor.${prefix}_feeding_last`]?.state);
  const interval = stateNumber(
    states[`number.${prefix}_feeding_interval_hours`]?.state,
    DEFAULT_INTERVAL_HOURS
  );
  const now = new Date();
  const lines = [];
  if (last === null) {
    lines.push("**No feeding logged yet — press the feeding button.**");
  } else {
    lines.push(`**Last feeding: ${timeSince(last, now)} ago**`);
    lines.push("");
    // Same computation as dashboard.example.yaml: the displayed countdown
    // is last + interval (the 10-minute grace applies to the reminder
    // binary sensor only).
    const due = new Date(last.getTime() + interval * 3600 * 1000);
    const when = `${
      due.getDate() === now.getDate() ? "at" : "tomorrow at"
    } ${hhmm(due)}`;
    if (due.getTime() > now.getTime()) {
      lines.push(`## 🍼 Next feeding in ${timeUntil(due, now)} — ${when}`);
    } else {
      lines.push(`## ⚠️ OVERDUE by ${timeSince(due, now)} — was due ${when}`);
    }
  }
  lines.push("");
  lines.push(
    `<small>The reminder binary sensor (binary_sensor.${prefix}_feeding_due,`,
    `includes a 10-minute grace period) is ${
      states[`binary_sensor.${prefix}_feeding_due`]?.state === "on" ? "ON" : "off"
    }.</small>`
  );
  return {
    type: "markdown",
    title: `${name} - feeding status`,
    content: lines.join("\n"),
  };
}

function tileCard(entityId, name, icon) {
  return {
    type: "tile",
    entity: entityId,
    name,
    icon,
    tap_action: {
      action: "perform-action",
      perform_action: "button.press",
      target: { entity_id: entityId },
    },
    state_content: ["last_changed"],
  };
}

function buttonsCard(prefix) {
  const cards = [];
  for (const activity of ACTIVITIES) {
    cards.push(
      tileCard(
        `button.${prefix}_${activity}`,
        ACTIVITY_LABELS[activity],
        ACTIVITY_ICONS[activity]
      )
    );
  }
  for (const activity of ACTIVITIES) {
    cards.push(
      tileCard(
        `button.${prefix}_${activity}_undo`,
        `Undo ${ACTIVITY_LABELS[activity].toLowerCase()}`,
        UNDO_ICON
      )
    );
  }
  return { square: false, type: "grid", title: "Buttons", columns: 2, cards };
}

function settingsCard(name, prefix) {
  return {
    type: "entities",
    title: `${name} settings`,
    entities: [
      {
        entity: `number.${prefix}_feeding_interval_hours`,
        name: "Feeding interval (hours)",
      },
    ],
  };
}

function chartCard(title, prefix, activity) {
  return {
    type: "statistics-graph",
    title,
    entities: [
      {
        entity: `sensor.${prefix}_${activity}_total`,
        name: TOTAL_CHART_NAMES[activity],
      },
    ],
    chart_type: "bar",
    period: "day",
    stat_types: ["sum"],
    days_to_show: 14,
  };
}

function logbookCard(name, prefix) {
  return {
    type: "logbook",
    title: `${name} timeline`,
    hours_to_show: 24,
    target: {
      entity_id: ACTIVITIES.map((a) => `button.${prefix}_${a}`),
    },
  };
}

function childSection(name, prefix, states) {
  return {
    type: "grid",
    column_span: 4,
    cards: [
      { type: "heading", heading_style: "title", heading: name },
      overviewCard(name, prefix, states),
      feedingStatusCard(name, prefix, states),
      buttonsCard(prefix),
      settingsCard(name, prefix),
      chartCard("Feedings/day", prefix, "feeding"),
      chartCard("Pee/day", prefix, "pee"),
      chartCard("Poo small/day", prefix, "poo_small"),
      chartCard("Poo large/day", prefix, "poo_large"),
      logbookCard(name, prefix),
    ],
  };
}

// ---------------------------------------------------------------------------
// The strategy element
// ---------------------------------------------------------------------------

class ActivityTrackerDashboardStrategy extends HTMLElement {
  // No settings to edit: everything is derived from the entities.
  static noEditor = true;

  static async generate(config, hass) {
    const cfg = config || {};
    const title = cfg.title || "Activity Tracker";
    const states = hass?.states || {};
    const children = findChildren(states);

    const sections = [];
    for (const prefix of [...children.keys()].sort()) {
      const name = childDisplayName(prefix, states, cfg);
      sections.push(childSection(name, prefix, states));
    }

    if (sections.length === 0) {
      sections.push({
        type: "grid",
        cards: [{ type: "markdown", content: PLACEHOLDER_CONTENT }],
      });
    }

    return { title, views: [{ type: "sections", sections }] };
  }

  // Live refresh on 2026.x frontends: state changes do not regenerate a
  // strategy by default, so re-run whenever one of our entities changed or
  // a registry update (new child, renamed prefix) happened. Older
  // frontends ignore this and re-generate when the dashboard loads.
  static shouldRegenerate(config, oldHass, newHass) {
    if (!oldHass || !newHass) {
      return true;
    }
    if (
      oldHass.entities !== newHass.entities ||
      oldHass.devices !== newHass.devices
    ) {
      return true;
    }
    const oldStates = oldHass.states || {};
    const newStates = newHass.states || {};
    for (const entityId of Object.keys(newStates)) {
      const dot = entityId.indexOf(".");
      if (DOMAIN_SUFFIXES[entityId.slice(0, dot)] === undefined) {
        continue;
      }
      if (oldStates[entityId] !== newStates[entityId]) {
        return true;
      }
    }
    return false;
  }

  // 2026.5+ Add-dashboard dialog: pre-fill the new dashboard's title/icon.
  static getCreateSuggestions(hass) {
    return { title: "Activity Tracker", icon: "mdi:baby-bottle-outline" };
  }

  // Minimal default configuration (strategy-picker previews, if asked for).
  static getStubConfig(hass) {
    return { type: `custom:${STRATEGY_NAME}`, title: "Activity Tracker" };
  }

  // No visual editor for the (optional) strategy options.
  static getConfigElement() {
    return null;
  }
}

// Register with the 2026.5+ Add-dashboard picker SYNCHRONOUSLY, at script
// load — the dialog reads window.customStrategies the moment it opens (its
// resource wait can resolve instantly), so an async registration loses the
// race and the entry never shows. The push needs no card helpers.
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((s) => s && s.type === STRATEGY_NAME)) {
  window.customStrategies.push({
    type: STRATEGY_NAME,
    strategyType: "dashboard",
    name: "Activity Tracker",
    description:
      "Buttons, counters, daily charts, timeline and feeding reminders — one section per child, fully automatic.",
    documentationURL:
      "https://github.com/majk-p/home-assistant-addons/tree/main/custom_components/activity_tracker",
  });
}

function registerStrategy() {
  // The resource may be loaded twice (extra module URL + a manually added
  // resource); defining the same tag twice throws.
  if (customElements.get(ELEMENT_TAG)) {
    return;
  }
  customElements.define(ELEMENT_TAG, ActivityTrackerDashboardStrategy);
}

if (typeof window.loadCardHelpers === "function") {
  // Make sure the dashboard machinery is ready before defining the element
  // (harmless for strategies, matches the custom-card conventions).
  Promise.resolve(window.loadCardHelpers())
    .catch(() => {})
    .then(registerStrategy);
} else {
  registerStrategy();
}
