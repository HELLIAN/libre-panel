// Settings → Weather and place: the place first (the weather needs it, and the sun and the moon
// use it as well), then the weather (off until it is turned on), where it comes from (Open-Meteo,
// or OpenWeatherMap with the person's own API key), its units and how often it is asked for,
// then the look of the weather symbols and the moon (it takes effect at once). Searching a place
// sends the name that was typed to Open-Meteo, and the weather sends the place's coordinates to
// the service chosen (with the key, to OpenWeatherMap): nothing else leaves this computer for
// either, and neither happens unasked. The page is told whether a key is saved, never the key.
// The sources are made when the panel starts, so a change takes effect after a restart.

import { endpoints } from "../../core/api.js";
import { formatNumber, t } from "../../core/i18n.js";
import { loadSettings, saveSettings, searchPlaces, useConfig } from "../../core/settings.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Segmented, Toggle } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Card, Note, RestartNote, SettingRow } from "./parts.js";

const INTERVALS = [5, 15, 30, 60];

const coordinates = (latitude, longitude) =>
  `${formatNumber(latitude, 2, { fixed: true })}, ${formatNumber(longitude, 2, { fixed: true })}`;

const OWM = "openweathermap";
// the services the weather can come from, by their own names (not translated)
const SERVICES = { "open-meteo": "Open-Meteo", [OWM]: "OpenWeatherMap" };

/** What the weather's switch says: what goes where, and what is missing first. */
function weatherHint({ hasPlace, enabled, minutes, owm, keyed }) {
  if (owm) {
    if (!keyed) return t("Give your OpenWeatherMap API key below first.");
    if (!hasPlace) return t("Choose a place first. Off until you turn it on; then only its coordinates and your key go to OpenWeatherMap.");
    return enabled
      ? t("Only your key and the coordinates of your place go to OpenWeatherMap, every {minutes} minutes. Nothing else leaves this computer for it.", { minutes })
      : t("Off until you turn it on. Then only your key and the coordinates of your place go to OpenWeatherMap, every {minutes} minutes.", { minutes });
  }
  if (!hasPlace) return t("Choose a place first. Off until you turn it on; then only its coordinates go to Open-Meteo.");
  return enabled
    ? t("Only the coordinates of your place go to Open-Meteo, every {minutes} minutes. Nothing else leaves this computer for it.", { minutes })
    : t("Off until you turn it on. Then only the coordinates of your place go to Open-Meteo, every {minutes} minutes.", { minutes });
}

/** OpenWeatherMap's API key: whether one is saved (never the key itself: the page is not given
 * it), a field to give one or another, and a way to take it out. */
function KeyRow({ saved, enabled }) {
  const [editing, setEditing] = useState(!saved);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => setEditing(!saved), [saved]);
  const store = async () => {
    const given = key.trim();
    if (!given || busy) return;
    setBusy(true);
    const ok = await saveSettings({ "weather.api_key": given });
    setBusy(false);
    if (ok) {
      setKey("");
      setEditing(false);
    }
  };
  const remove = async () => {
    const sure =
      !enabled ||
      (await confirmDialog({
        title: t("Remove the key?"),
        message: t("OpenWeatherMap needs it, so the weather switches off with it."),
        confirm: t("Remove"),
        danger: true,
      }));
    if (sure) await saveSettings({ "weather.enabled": false, "weather.api_key": null });
  };
  return html`<${SettingRow}
    title=${t("API key")}
    hint=${html`${t("A free key of your own, from")} <a href="https://home.openweathermap.org/api_keys" target="_blank" rel="noopener noreferrer">openweathermap.org</a>. ${t("It stays in config.toml on this computer and goes only to OpenWeatherMap. A new key can take a few hours before it works.")}`}
    stacked
  >
    ${editing
      ? html`<form
          class="place-search"
          onSubmit=${(event) => {
            event.preventDefault();
            store();
          }}
        >
          <input
            type="password"
            class="input"
            aria-label=${t("Your OpenWeatherMap API key")}
            placeholder=${t("Your OpenWeatherMap API key")}
            value=${key}
            maxlength="128"
            autocomplete="off"
            spellcheck="false"
            onInput=${(event) => setKey(event.currentTarget.value)}
          />
          <button type="submit" class="btn" disabled=${busy || !key.trim()}>${t("Save")}</button>
          ${saved
            ? html`<button type="button" class="link-btn" onClick=${() => (setKey(""), setEditing(false))}>${t("Cancel")}</button>`
            : null}
        </form>`
      : html`<div class="key-saved">
          <span>${t("A key is saved.")}</span>
          <button type="button" class="link-btn" onClick=${() => setEditing(true)}>${t("Replace")}</button>
          <button type="button" class="link-btn" onClick=${remove}>${t("Remove")}</button>
        </div>`}
  <//>`;
}

function WeatherCard() {
  const config = useConfig();
  if (!config) return null;
  const { enabled, latitude, longitude, units, update_minutes: minutes, provider, api_key_set: keyed } = config.weather;
  const hasPlace = latitude !== null && longitude !== null;
  const owm = provider === OWM;
  const intervals = INTERVALS.includes(minutes) ? INTERVALS : [...INTERVALS, minutes].sort((a, b) => a - b);
  return html`<${Card} id="weather">
      <${SettingRow} title=${t("Weather")} hint=${weatherHint({ hasPlace, enabled, minutes, owm, keyed })}>
        <${Toggle}
          label=${t("Weather")}
          checked=${enabled}
          disabled=${(!hasPlace || (owm && !keyed)) && !enabled}
          onChange=${(on) => saveSettings({ "weather.enabled": on })}
        />
      <//>
      <${SettingRow}
        title=${t("Source")}
        hint=${owm ? t("OpenWeatherMap needs an API key of your own; a free one is enough.") : t("Open-Meteo needs no key.")}
      >
        <${Segmented}
          label=${t("Source")}
          value=${owm ? OWM : "open-meteo"}
          onChange=${(value) => saveSettings({ "weather.provider": value })}
          options=${Object.entries(SERVICES).map(([value, name]) => ({ value, label: name }))}
        />
      <//>
      ${owm ? html`<${KeyRow} saved=${keyed} enabled=${enabled} />` : null}
      <${SettingRow} title=${t("Units")} hint=${t("Temperatures and wind on the panel.")}>
        <${Segmented}
          label=${t("Units")}
          value=${units}
          onChange=${(value) => saveSettings({ "weather.units": value })}
          options=${[
            { value: "metric", label: "°C, km/h" },
            { value: "imperial", label: "°F, mph" },
          ]}
        />
      <//>
      <${SettingRow} title=${t("Update")} hint=${t("How often the weather is asked for.")}>
        <${Segmented}
          label=${t("Update")}
          value=${minutes}
          onChange=${(value) => saveSettings({ "weather.update_minutes": value })}
          options=${intervals.map((value) => ({ value, label: t("{minutes} min", { minutes: value }) }))}
        />
      <//>
    <//>
    <p class="settings-credit">
      ${owm
        ? html`${t("Weather data by")} <a href="https://openweathermap.org" target="_blank" rel="noopener noreferrer">OpenWeather</a> (ODbL) · ${t("places by")} <a href="https://open-meteo.com" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a> (CC BY 4.0)`
        : html`${t("Weather data by")} <a href="https://open-meteo.com" target="_blank" rel="noopener noreferrer">Open-Meteo.com</a> (CC BY 4.0)`}
    </p>`;
}

// -- the look of the symbols -------------------------------------------------------------------

// config.toml's ``symbols`` (config.SYMBOL_PACKS), the first first; "vivid" is the default
const PACKS = [
  { id: "classic", label: () => t("Classic"), hint: () => t("the first: calm and simple") },
  { id: "vivid", label: () => t("Vivid"), hint: () => t("sharper, with more to it: a corona, snow crystals, the moon as it looks") },
];

/** The look of the weather symbols and the moon, tiles with a picture of each; a theme may give a
 * symbol a look of its own. */
function SymbolsCard() {
  const config = useConfig();
  if (!config) return null;
  const current = PACKS.some((pack) => pack.id === config.symbols) ? config.symbols : "vivid";
  return html`<${Card} id="symbols" title=${t("Weather symbols and moon")}
      text=${t("How they look on the panel, where a theme leaves it to you. The panel takes it at once.")}>
    <div class="fan-look-tiles symbol-pack-tiles" role="radiogroup" aria-label=${t("Look of the weather symbols")}>
      ${PACKS.map(
        (pack) => html`<button type="button" role="radio" key=${pack.id} class="fan-look-tile symbol-pack-tile" data-pack=${pack.id}
          aria-checked=${String(pack.id === current)} onClick=${() => pack.id !== current && saveSettings({ symbols: pack.id })}>
          <img src=${endpoints.symbolsLookUrl(pack.id)} alt="" width="240" height="80" decoding="async" />
          <span>${pack.label()}</span>
          <span class="muted">${pack.hint()}</span>
        </button>`,
      )}
    </div>
  <//>`;
}

// -- the place -----------------------------------------------------------------------------------

function PlaceCard() {
  const config = useConfig();
  const [query, setQuery] = useState("");
  const [found, setFound] = useState(null); // null: not searched yet
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  if (!config) return null;
  const { enabled, latitude, longitude, place } = config.weather;
  const hasPlace = latitude !== null && longitude !== null;
  const search = async () => {
    if (busy || query.trim().length < 2) return;
    setBusy(true);
    setProblem("");
    try {
      setFound(await searchPlaces(query.trim()));
    } catch (error) {
      setFound(null);
      setProblem(error.message);
    }
    setBusy(false);
  };
  const choose = async (item) => {
    const ok = await saveSettings({
      "weather.latitude": item.latitude,
      "weather.longitude": item.longitude,
      "weather.place": item.name,
    });
    if (ok) {
      setFound(null);
      setQuery("");
    }
  };
  const remove = async () => {
    const sure =
      !enabled ||
      (await confirmDialog({
        title: t("Remove the place?"),
        message: t("The weather needs a place, so it switches off with it."),
        confirm: t("Remove"),
        danger: true,
      }));
    if (sure) {
      await saveSettings({
        "weather.enabled": false,
        "weather.latitude": null,
        "weather.longitude": null,
        "weather.place": null,
      });
    }
  };
  return html`<${RestartNote} prefix="weather." />
    <${Card} id="place" title=${t("Place")} text=${t("For the weather, and for sunrise, sunset and the moon, also while the weather is off.")}>
    <${SettingRow}
      title=${hasPlace ? place || t("A place set in config.toml") : t("No place yet")}
      hint=${hasPlace ? coordinates(latitude, longitude) : t("Search for a town or a district below.")}
    >
      ${hasPlace ? html`<button type="button" class="link-btn" onClick=${remove}>${t("Remove")}</button>` : null}
    <//>
    <${SettingRow}
      title=${hasPlace ? t("Another place") : t("Search")}
      hint=${t("The name you type goes to the place search of Open-Meteo, and only when you press Search. The place is kept to about a kilometre.")}
      stacked
    >
      <form
        class="place-search"
        onSubmit=${(event) => {
          event.preventDefault();
          search();
        }}
      >
        <input
          type="text"
          class="input"
          aria-label=${t("Name of a place")}
          placeholder=${t("Name of a place")}
          value=${query}
          maxlength="100"
          spellcheck="false"
          onInput=${(event) => setQuery(event.currentTarget.value)}
        />
        <button type="submit" class="btn" disabled=${busy || query.trim().length < 2}>${busy ? t("Searching …") : t("Search")}</button>
      </form>
      ${problem ? html`<${Note} tone="error">${problem}<//>` : null}
      ${found && !found.length ? html`<span class="muted">${t("Nothing found. Try another spelling, or only the town.")}</span>` : null}
      ${found && found.length
        ? html`<ul class="places" aria-label=${t("Places found")}>
            ${found.map(
              (item) => html`<li key=${`${item.latitude},${item.longitude},${item.name}`}>
                <button type="button" onClick=${() => choose(item)}>
                  <span>${item.name}</span>
                  <span class="muted mono">${coordinates(item.latitude, item.longitude)}</span>
                </button>
              </li>`,
            )}
          </ul>`
        : null}
    <//>
  <//>`;
}

export const weatherGroups = [
  { id: "place", order: 10, component: PlaceCard, load: loadSettings },
  { id: "weather", order: 20, component: WeatherCard },
  { id: "symbols", order: 30, component: SymbolsCard },
];
