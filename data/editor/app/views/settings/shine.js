// Settings → Panel → Moving light: the light that moves over a panel that moves ([shine] in
// config.toml, render/shine.py): a ring's end that breathes and a sweep along it, a graph's end
// that beats, the cover that glows while music plays, a download's sheen, the sun's glow and
// glint. Each kind on or off; a theme may show fewer (its animation.shine).

import { t } from "../../core/i18n.js";
import { saveSettings, useConfig } from "../../core/settings.js";
import { html } from "../../lib/preact.js";
import { Toggle } from "../../ui/controls.js";
import { Card, SettingRow, Status } from "./parts.js";

const KINDS = [
  {
    id: "rings",
    title: () => t("Rings"),
    hint: () => t("The end of a ring breathes in its colour, and now and then a light runs along it. Over 80 % its end glows wider."),
  },
  { id: "graphs", title: () => t("Graphs"), hint: () => t("The newest point of a curve beats with each reading.") },
  {
    id: "music",
    title: () => t("Music"),
    hint: () => t("While a track plays, its cover glows in its own colours, three bars dance after its state and a light runs along its progress."),
  },
  { id: "downloads", title: () => t("Downloads"), hint: () => t("A sheen runs along a download's progress, the faster the faster it loads.") },
  { id: "sun", title: () => t("Sun and moon"), hint: () => t("The sun on its way, or the moon, glows softly, and a glint runs along the way the day has gone.") },
];

export function ShineCard() {
  const config = useConfig();
  if (!config) return null;
  const chosen = config.shine || {};
  const calm = config.device?.motion === "calm";
  return html`<${Card}
    id="panel-shine"
    title=${t("Moving light")}
    text=${t("Light that moves over rings, curves, music, downloads and the sun. Made to cost little: each light is made once and only laid anew where it moves.")}
  >
    ${calm ? html`<div class="settings-row"><${Status}>${t("Calm motion: nothing moves on this panel.")}<//></div>` : null}
    ${KINDS.map(
      (kind) => html`<${SettingRow} key=${kind.id} class="shine-kind" data-kind=${kind.id} title=${kind.title()} hint=${kind.hint()}>
        <${Toggle} label=${kind.title()} checked=${chosen[kind.id] !== false} onChange=${(on) => saveSettings({ [`shine.${kind.id}`]: on })} />
      <//>`,
    )}
  <//>`;
}
