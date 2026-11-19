// Settings → Panel → Start screen: the panel's own two screens in a look chosen here, made for
// its size: the boot logo it shows when it is switched on, and the standby clip it plays when
// the PC starts, until Libre Panel shows themes (devices/panel_files.py). Each is written only
// on a click, after a question; the panel's themes are paused for it, and the panel then shows
// what was written until "Show the themes again". A panel model no one has tried is written to
// only where its owner agrees, each time, after being told what happened to issue 52's 9.2"; and
// whoever tries one, or the 8.8", is asked to share how it went, in a form filled in for them.

import { appAction } from "../../core/app.js";
import { endpoints } from "../../core/api.js";
import { t } from "../../core/i18n.js";
import { useMounted } from "../../core/orientation.js";
import { useConfig } from "../../core/settings.js";
import { useTarget } from "../../core/target.js";
import { html, useEffect, useState } from "../../lib/preact.js";
import { Select } from "../../ui/controls.js";
import { confirmDialog } from "../../ui/dialog.js";
import { Icon } from "../../ui/icons.js";
import { toast, toastError } from "../../ui/toast.js";
import { Card, Note } from "./parts.js";

const kilobytes = (bytes) => `${Math.max(1, Math.round(bytes / 1024))} KB`;

// The looks the screens come in (render/bootscreen.py, bootlooks.py): their names, and what their
// clip does
const LOOKS = {
  "libre-panel": [() => t("Libre Panel"), () => t("a light that runs out from the middle")],
  aurora: [() => t("Aurora"), () => t("curtains of light that shimmer")],
  pulse: [() => t("Pulse"), () => t("a light that sweeps along a heartbeat")],
  circuit: [() => t("Circuit"), () => t("light that runs along the traces into the chip")],
  night: [() => t("Night"), () => t("stars that twinkle under the moon")],
  levels: [() => t("Levels"), () => t("an equaliser's bars that rise and fall")],
  dark: [() => t("Dark"), () => t("the mark, faintly, breathing")],
};
const lookName = (id) => (LOOKS[id] ? LOOKS[id][0]() : id);
const lookMotion = (id) => (LOOKS[id] ? LOOKS[id][1]() : "");

function Tile({ title, text, picture, upright, children }) {
  return html`<div class=${`boot-tile${upright ? " upright" : ""}`}>
    <div class="boot-picture"><img src=${picture} alt=${title} decoding="async" /></div>
    <div class="boot-tile-text">
      <strong>${title}</strong>
      <span class="muted">${text}</span>
    </div>
    <div class="boot-actions">${children}</div>
  </div>`;
}

export function StartScreenCard() {
  const config = useConfig(); // (none while config.toml cannot be read: nothing here is written then)
  const way = useMounted() === "portrait" ? "portrait" : "landscape"; // drawn upright on a bar on end
  const panel = useTarget(); // the panel chosen in the header: its own logo and clip
  const [info, setInfo] = useState(null);
  const [model, setModel] = useState(null);
  const [busy, setBusy] = useState("");
  const [held, setHeld] = useState(false); // the themes are paused for what the panel shows
  const [found, setFound] = useState(null); // what "What is on the panel" read
  const [design, setDesign] = useState("libre-panel"); // the look both screens are made in
  const [written, setWritten] = useState(null); // what was written here to a model not confirmed, in which look
  useEffect(() => {
    setFound(null);
    setWritten(null);
    endpoints
      .startScreen(panel)
      .then((data) => {
        setInfo(data);
        setModel(data.model || data.models.find((item) => item.measured)?.id || data.models[0]?.id);
      })
      .catch(() => setInfo({ models: [] }));
  }, [panel]);
  if (!config || !info?.models?.length || !model) return null;
  const chosen = info.models.find((item) => item.id === model);
  // files are written to the models it has been done on (the 9.2" and the 12.3", and the 8.8" as
  // the 9.2"); to another only where its owner agrees to try it, at their own risk, each time
  // (or where the environment says so: then writes is true)
  const untested = chosen?.tried === false;
  const asks = untested && chosen?.writes !== true; // (the person's word is wanted for each write)
  const unconfirmed = chosen?.tried === true && chosen?.confirmed === false; // the 8.8"
  // the issue form of the start screen, filled in with what is known here (the form is English)
  const report = (what) => {
    const fields = { title: `Start screen: ${chosen?.label || model}`, model, version: info.version || "", look: what?.look || design };
    if (what) {
      fields.written = [what.logo ? `boot logo ${kilobytes(what.logo)}` : "", what.clip ? `standby clip ${kilobytes(what.clip)}` : ""]
        .filter(Boolean).join(", ");
    }
    return `${info.report}&${new URLSearchParams(fields)}`;
  };
  const share = (what) => info.report
    ? html`<a class="btn btn-primary" href=${report(what)} target="_blank" rel="noopener noreferrer" data-share="start-screen">
        ${t("Share how it went")} <${Icon} name="external" size=${14} /></a>`
    : null;

  const run = async (action, question) => {
    const writes = action === "logo" || action === "clip";
    // a model no one has tried: what may happen is said first, and the write is its owner's
    const risky = writes && asks;
    const asked = risky
      ? {
        title: t("Write to a panel no one has tried?"),
        message: html`${t("No one has written start screens to this kind of panel yet. Libre Panel makes the files small and checks the panel before and after, but how much room it has for them is not known. On one 9.2\", files that left it without room stopped it from starting at all, and nothing sent from the PC reached it again (issue 52). Write only if you accept that risk.")}<br /><br />${question.message}`,
        confirm: t("Write at my own risk"),
        danger: true,
      }
      : question;
    if (asked && !(await confirmDialog(asked))) return;
    setBusy(action);
    try {
      const answer = await endpoints.startScreenAction(action, model, panel, design, risky);
      if (info.runs_panel) setHeld(true);
      if (writes && (untested || unconfirmed)) {
        setWritten((was) => ({ ...(was || {}), [action]: action === "logo" ? answer.boot_logo : answer.standby_clip, look: design }));
      }
      if (action === "state") setFound(answer);
      else if (action === "logo") toast(t("The boot logo is on the panel ({size}); it shows at the next power-on.", { size: kilobytes(answer.boot_logo) }), { kind: "success" });
      else if (action === "remove-logo") toast(t("The factory logo shows again at the next power-on."), { kind: "success" });
      else toast(t("The standby clip is on the panel ({size}) and plays now; the panel starts with it.", { size: kilobytes(answer.standby_clip) }), { kind: "success" });
    } catch (error) {
      if (info.runs_panel) setHeld(true);
      toastError(error);
    } finally {
      setBusy("");
    }
  };
  const resume = async () => {
    await appAction("resume");
    setHeld(false);
  };
  const writing = Boolean(busy);
  // a look with more detail than fits finely in the room its files get on this panel: said,
  // and neither written nor made as a file
  const unfit = Boolean(chosen?.unfit?.includes(design));
  const more = (config.panels || []).length > 0; // more panels: this card is the one chosen at the top
  // a panel mounted upside down: its logo and clip are written turned round, as its frames are
  const turned = (panel > 1 ? (config.panels || [])[panel - 2]?.rotate : config.device?.rotate) === 180;
  return html`<${Card} id="start-screen"
    title=${more ? `${t("Start screen")} · ${t("Panel {number}", { number: panel })}` : t("Start screen")}
    text=${t("The panel's own screens, in the look you choose and made for its size: the logo it shows when it is switched on, and the clip it plays when the PC starts, until Libre Panel shows its themes.")}>
    ${more ? html`<${Note}>${t("For the panel chosen at the top; choose another there for its own logo and clip.")}<//>` : null}
    <div class="boot-body">
    <div class="boot-panel">
      <span class="muted">${t("Made for")}</span>
      <${Select} label=${t("Panel")} value=${model} onChange=${(id) => { setModel(id); setFound(null); setWritten(null); }}
        options=${info.models.map((item) => {
          const [w, h] = way === (info.orientation || "landscape") ? item.size : [item.size[1], item.size[0]];
          return { value: item.id, label: `${item.label} · ${w} × ${h}` };
        })} />
    </div>
    ${written
      ? html`<${Note} action=${share(written)}>${t("Written. Switch the PC fully off and on again: does the panel show the boot logo when it comes on, and play the clip until Libre Panel starts? Please share how it went, also if it did not work.")}<//>`
      : untested
        ? html`<${Note} action=${share(null)}>${t("No one has tried start screens on this panel yet, and how much room it has for them is not known. You can try it at your own risk: how it went decides whether Libre Panel writes them to this panel for everyone.")}<//>`
        : unconfirmed
          ? html`<${Note} action=${share(null)}>${t("Start screens are written to this panel as to the 9.2\", its sibling, but no one has said yet how they work on one. If you try it, please share how it went.")}<//>`
          : null}
    ${turned
      ? html`<${Note}>${t("The panel is mounted upside down: the logo and the clip are written turned round, so that they are upright on it. Write them again when you turn the panel round.")}<//>`
      : null}
    <div class="fan-look-tiles boot-look-tiles" role="radiogroup" aria-label=${t("Look of the start screen")}>
      ${(info.designs || ["libre-panel"]).map(
        (id) => html`<button type="button" role="radio" key=${id} class="fan-look-tile boot-look-tile" data-look=${id}
          aria-checked=${String(id === design)} onClick=${() => setDesign(id)}>
          <img src=${endpoints.startScreenPicture(model, "logo", 320, way, panel, id)} alt="" decoding="async" loading="lazy" />
          <span>${lookName(id)}</span>
        </button>`,
      )}
    </div>
    ${unfit
      ? html`<${Note}>${t("This look has more detail than fits in the room Libre Panel uses on this panel, and it is not written coarser: choose another look.")}<//>`
      : null}
    ${held
      ? html`<${Note} action=${html`<button type="button" class="btn btn-primary" onClick=${resume}>${t("Show the themes again")}</button>`}>
          ${t("The panel shows what was written; its themes are paused.")}
        <//>`
      : null}
    <div class="boot-tiles">
      <${Tile} title=${t("Boot logo")} text=${t("When the panel is switched on. The factory logo stays on the panel and can always come back.")}
        picture=${endpoints.startScreenPicture(model, "logo", 960, way, panel, design)} upright=${way === "portrait"}>
        <button type="button" class="btn btn-primary" disabled=${writing || unfit}
          onClick=${() => run("logo", {
            title: t("Write the boot logo to the panel?"),
            message: t("It shows when the panel is switched on. The factory logo is not touched: \"Factory logo again\" brings it back."),
            confirm: t("Write it"),
          })}>
          ${busy === "logo" ? t("Writing…") : t("Put on the panel")}
        </button>
        <button type="button" class="btn" disabled=${writing}
          onClick=${() => run("remove-logo", { title: t("Bring back the factory logo?"), message: t("Libre Panel's boot logo is taken off the panel."), confirm: t("Factory logo again") })}>
          ${t("Factory logo again")}
        </button>
        ${unfit ? null : html`<a class="btn" href=${endpoints.startScreenFile(model, "logo", way, panel, design)} download><${Icon} name="download" size=${15} /> JPEG</a>`}
      <//>
      <${Tile} title=${t("Standby clip")} text=${t("When the PC starts, until Libre Panel shows its themes: {motion}.", { motion: lookMotion(design) })}
        picture=${endpoints.startScreenPicture(model, "clip", 960, way, panel, design)} upright=${way === "portrait"}>
        <button type="button" class="btn btn-primary" disabled=${writing || unfit}
          onClick=${() => run("clip", {
            title: t("Write the standby clip to the panel?"),
            message: t("It replaces the clip the panel plays when the PC starts ({name}); the one there now cannot be read back first. The panel is set to start with it.", { name: info.names.clip }),
            confirm: t("Write it"),
            danger: true,
          })}>
          ${busy === "clip" ? t("Writing…") : t("Put on the panel")}
        </button>
        ${unfit ? null : html`<a class="btn" href=${endpoints.startScreenFile(model, "clip", way, panel, design)} download><${Icon} name="download" size=${15} /> H.264</a>`}
      <//>
    </div>
    <div class="boot-foot">
      <button type="button" class="btn" disabled=${writing} onClick=${() => run("state")}>
        ${busy === "state" ? t("Asking the panel…") : t("What is on the panel")}
      </button>
      ${found
        ? html`<span class="muted">
            ${t("Boot logo: {logo} · standby clip: {clip}", {
              logo: found.boot_logo ? kilobytes(found.boot_logo) : t("the factory's"),
              clip: found.standby_clip ? kilobytes(found.standby_clip) : t("none"),
            })}
            ${found.files?.length ? html` · ${found.files.join(", ")}` : null}
          </span>`
        : null}
    </div>
    ${found?.room && (found.boot_logo > found.room.logo || found.standby_clip > found.room.clip)
      ? html`<${Note}>${t("The panel holds larger files than Libre Panel writes now. They can stay; written again, they become smaller.")}<//>`
      : null}
    </div>
  <//>`;
}
