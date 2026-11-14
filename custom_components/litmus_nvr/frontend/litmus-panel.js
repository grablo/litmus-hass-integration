/*
 * Litmus NVR sidebar panel — one button that opens the NVR's own web UI (http://<nvr>:8128) in a new tab, and its address.
 * The page is not shown inside Home Assistant: the NVR sends X-Frame-Options: DENY (clickjacking protection for its sign-in page),
 * and the Home Assistant address differs per home (IP, domain, reverse proxy) so it cannot be narrowed to "Home Assistant only".
 * Showing it inside Home Assistant is the add-on's job (ingress — docs/LITMUS_HOME_INTEGRATION.md §17.11 D1).
 * Uses no Home Assistant internals except <ha-menu-button> (the sidebar toggle on phones, as other custom panels do).
 */
const OPEN = {
  en: "Open Litmus NVR", ko: "Litmus NVR 열기", de: "Litmus NVR öffnen", es: "Abrir Litmus NVR", fr: "Ouvrir Litmus NVR",
  id: "Buka Litmus NVR", it: "Apri Litmus NVR", ja: "Litmus NVR を開く", nl: "Litmus NVR openen", pl: "Otwórz Litmus NVR",
  "pt-BR": "Abrir o Litmus NVR", ru: "Открыть Litmus NVR", th: "เปิด Litmus NVR", tr: "Litmus NVR'yi aç", vi: "Mở Litmus NVR",
  "zh-Hans": "打开 Litmus NVR", "zh-Hant": "開啟 Litmus NVR",
};

function label(lang) {
  if (!lang) return OPEN.en;
  if (OPEN[lang]) return OPEN[lang];
  if (lang.startsWith("zh")) return /hant|tw|hk/i.test(lang) ? OPEN["zh-Hant"] : OPEN["zh-Hans"];
  if (lang.startsWith("pt")) return OPEN["pt-BR"];
  return OPEN[lang.split("-")[0]] || OPEN.en;
}

class LitmusNvrPanel extends HTMLElement {
  set hass(hass) { this._hass = hass; this._render(); }
  set narrow(narrow) { this._narrow = narrow; this._render(); }
  set panel(panel) { this._panel = panel; this._render(); }

  _render() {
    if (!this._panel || !this._hass) return;
    /*
     * The NVR runs as a Home Assistant add-on — open its screen through ingress, inside Home Assistant (no second sign-in, works from
     * any browser and remotely). Its address is the add-on hostname, which only resolves inside HAOS (2026-11-14 HAOS 18.4 test).
     * Home Assistant 2026 shows add-ons under /app/<slug>/ingress; older releases under /hassio/ingress/<slug>.
     */
    const addon = this._panel.config && this._panel.config.addon;
    if (addon) {
      const path = this._hass.panels && this._hass.panels.app ? `/app/${addon}/ingress` : `/hassio/ingress/${addon}`;
      if (window.location.pathname !== path) {
        window.history.replaceState(null, "", path);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: true } }));
      }
      return;
    }
    const url = (this._panel.config && this._panel.config.url) || "";
    const key = `${url}|${!!this._narrow}|${this._hass.language}`;
    if (this._key === key) {
      const btn = this.querySelector("ha-menu-button");
      if (btn) { btn.hass = this._hass; btn.narrow = this._narrow; }
      return;
    }
    this._key = key;
    this.innerHTML = "";
    this.style.cssText = "display:flex;flex-direction:column;min-height:100vh;background:var(--primary-background-color);";
    if (this._narrow) {
      const bar = document.createElement("div");
      bar.style.cssText = "display:flex;align-items:center;height:56px;padding:0 4px;background:var(--app-header-background-color);color:var(--app-header-text-color);";
      const menu = document.createElement("ha-menu-button");
      menu.hass = this._hass;
      menu.narrow = this._narrow;
      const title = document.createElement("div");
      title.textContent = "Litmus NVR";
      title.style.cssText = "font-size:20px;margin-left:8px;";
      bar.append(menu, title);
      this.append(bar);
    }
    const box = document.createElement("div");
    box.style.cssText = "margin:auto;padding:24px;text-align:center;color:var(--primary-text-color);";
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = label(this._hass.language);
    a.style.cssText = "display:inline-block;padding:12px 24px;border-radius:8px;background:var(--primary-color);color:var(--text-primary-color,#fff);text-decoration:none;font-size:16px;";
    const addr = document.createElement("p");
    addr.textContent = url.replace(/^https?:\/\//, "").replace(/\/$/, "");
    addr.style.cssText = "margin:12px 0 0;color:var(--secondary-text-color);";
    box.append(a, addr);
    this.append(box);
  }
}

if (!customElements.get("litmus-nvr-panel")) customElements.define("litmus-nvr-panel", LitmusNvrPanel);
