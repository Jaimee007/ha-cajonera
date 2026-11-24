// cajonera-card: Lovelace card for the modular drawer cabinet.
// Search box, drawer grid (A1 bottom-left), drawer contents with "Retirar",
// and the low-stock list. Talks only to the cajonera.* services.
// See docs/09_gestion_inventario.md section 8.
//
// Install: copy this file and cajonera-layout.js to <config>/www/, add
// /local/cajonera-card.js as a "JavaScript module" resource, then use
//   type: custom:cajonera-card

import { fmtQty, gridCells, rowLabel } from "./cajonera-layout.js";

const MAP = "sensor.cajonera_mapa";
const LOW = "sensor.cajonera_existencias_bajas";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .search { display: flex; gap: 8px; }
  .search input { flex: 1; min-width: 0; font: inherit; padding: 8px 10px; border-radius: 8px;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  button { font: inherit; padding: 6px 12px; border-radius: 8px; border: 0; cursor: pointer;
    background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); }
  button:disabled { opacity: .5; cursor: default; }
  .phrase { margin: 10px 0 0; color: var(--secondary-text-color); }
  .rows { list-style: none; margin: 8px 0 0; padding: 0; }
  .rows li { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--divider-color); flex-wrap: wrap; }
  .rows .name { flex: 1 1 140px; min-width: 0; }
  .rows .where { font-weight: 600; min-width: 2.5em; }
  .rows .qty { min-width: 4em; text-align: right; font-variant-numeric: tabular-nums; }
  .low { color: var(--error-color, #db4437); font-weight: 600; }
  .rows select { font: inherit; padding: 4px; border-radius: 6px; }
  .gridwrap { display: grid; grid-template-columns: auto 1fr; gap: 4px; margin-top: 16px; }
  .rowlabels, .collabels { display: grid; gap: 4px; color: var(--secondary-text-color); font-size: .85em; }
  .rowlabels { align-items: center; }
  .collabels { grid-column: 2; text-align: center; }
  .grid { display: grid; gap: 4px; }
  .cell { aspect-ratio: 1 / 1; min-height: 44px; border-radius: 8px; border: 2px solid var(--divider-color);
    display: flex; flex-direction: column; align-items: center; justify-content: center; cursor: pointer;
    background: var(--secondary-background-color); font-weight: 600; position: relative; overflow: hidden; }
  .cell.wide { aspect-ratio: auto; }
  .cell .lamp { position: absolute; inset: 0; opacity: .55; }
  .cell span { position: relative; }
  .cell.bajo { border-color: var(--error-color, #db4437); box-shadow: inset 0 0 0 2px var(--error-color, #db4437); }
  .cell.error { border-style: dashed; }
  .cell.selected { outline: 3px solid var(--primary-color); outline-offset: 1px; }
  .cell.buscado .lamp, .cell.buscado_bajo .lamp { animation: blink 1s steps(2, start) infinite; }
  .cell.buscado_bajo .lamp { animation-name: alt; }
  @keyframes blink { 50% { opacity: .1; } }
  @keyframes alt { 0%, 49% { background: rgb(0, 0, 255); } 50%, 100% { background: rgb(255, 120, 0); } }
  .base { font-size: .7em; font-weight: 400; }
  h3 { margin: 16px 0 4px; font-size: 1em; }
  .unplaced { color: var(--secondary-text-color); font-size: .9em; margin-top: 8px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
  .empty { color: var(--secondary-text-color); }
`;

class CajoneraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._results = null; // { frase, resultados }
    this._selected = null; // address
    this._contents = null; // { cajon, piezas }
    this._busy = false;
    this._stamp = "";
  }

  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 8;
  }

  set hass(hass) {
    this._hass = hass;
    const stamp = [MAP, LOW].map((id) => hass.states[id]?.last_updated ?? "").join("|");
    if (stamp !== this._stamp) {
      this._stamp = stamp;
      this._render();
    }
  }

  async _call(service, data = {}) {
    this._busy = true;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "call_service",
        domain: "cajonera",
        service,
        service_data: data,
        return_response: true,
      });
      return res?.response ?? {};
    } catch (err) {
      this._toast(`Error: ${err?.message ?? err}`);
      return null;
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _callNoResponse(service, data = {}) {
    try {
      await this._hass.callService("cajonera", service, data);
    } catch (err) {
      this._toast(`Error: ${err?.message ?? err}`);
    }
  }

  _toast(message) {
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  async _search(text) {
    const res = await this._call("inventario_buscar", { texto: text, origen: "panel" });
    if (res) this._results = res;
    this._render();
  }

  async _withdraw(stock, cantidad) {
    const res = await this._call("inventario_retirar", { stock, cantidad, origen: "panel" });
    if (!res) return;
    this._toast(res.frase);
    if (res.hecho) {
      if (this._results?.resultados) {
        this._results.resultados = this._results.resultados.map((r) =>
          r.stock === stock ? { ...r, cantidad: r.cantidad - cantidad } : r,
        );
      }
      if (this._selected !== null) await this._openDrawer(this._selected, false);
    }
    this._render();
  }

  async _openDrawer(address, identify = true) {
    this._selected = address;
    if (identify) this._callNoResponse("identificar", { direccion: address });
    const res = await this._call("contenido_cajon", { direccion: address });
    this._contents = res;
    this._render();
  }

  _qtyPicker(stock) {
    const opts = [1, 2, 3, 4, 5, 10].map((n) => `<option value="${n}">${n}</option>`).join("");
    return `<select data-qty="${stock}" aria-label="Cantidad">${opts}<option value="other">otra…</option></select>
      <button data-withdraw="${stock}" ${this._busy ? "disabled" : ""}>Retirar</button>`;
  }

  _rowsHtml(rows, showWhere) {
    if (!rows?.length) return "";
    return `<ul class="rows">${rows
      .map(
        (r) => `<li>
          <span class="name">${esc(r.pieza)}</span>
          ${showWhere ? `<span class="where">${esc(r.cajon)}</span>` : ""}
          <span class="qty">${fmtQty(r.cantidad)} uds</span>
          ${r.bajo ? `<span class="low">⚠ bajo</span>` : ""}
          ${this._qtyPicker(r.stock)}
        </li>`,
      )
      .join("")}</ul>`;
  }

  _gridHtml(cajones) {
    const g = gridCells(cajones);
    if (!g.cells.length) return `<p class="empty">No hay cajones todavía.</p>`;
    const rowLabels = Array.from({ length: g.rows }, (_, i) => `<div>${rowLabel(g.rows - 1 - i)}</div>`).join("");
    const colLabels = Array.from({ length: g.cols }, (_, i) => `<div>${i + 1}</div>`).join("");
    const cells = g.cells
      .map((c) => {
        const cls = ["cell", c.modo, c.bajo && "bajo", c.error && "error", c.colSpan > 1 && "wide",
          this._selected === c.direccion && "selected"].filter(Boolean).join(" ");
        const lamp = c.rgb.some((v) => v > 0) ? `<div class="lamp" style="background: rgb(${c.rgb.join(",")})"></div>` : "";
        return `<div class="${cls}" data-addr="${c.direccion}" role="button" tabindex="0"
            style="grid-column: ${c.col} / span ${c.colSpan}; grid-row: ${c.row} / span ${c.rowSpan}"
            title="${esc(c.nombre ?? "")} · dirección ${c.direccion}${c.error ? " · " + esc(c.error) : ""}">
            ${lamp}<span>${esc(c.nombre ?? "?")}</span>${c.direccion === 0 ? `<span class="base">base</span>` : ""}
          </div>`;
      })
      .join("");
    const unplaced = g.unplaced.length
      ? `<p class="unplaced">Sin posición: ${g.unplaced.map((c) => `cajón ${c.direccion}${c.error ? ` (${esc(c.error)})` : ""}`).join(", ")}</p>`
      : "";
    return `<div class="gridwrap">
        <div class="rowlabels" style="grid-template-rows: repeat(${g.rows}, 1fr)">${rowLabels}</div>
        <div class="grid" style="grid-template-columns: repeat(${g.cols}, 1fr); grid-template-rows: repeat(${g.rows}, auto)">${cells}</div>
        <div class="collabels" style="grid-template-columns: repeat(${g.cols}, 1fr)">${colLabels}</div>
      </div>${unplaced}`;
  }

  _render() {
    if (!this._hass) return;
    const map = this._hass.states[MAP];
    const low = this._hass.states[LOW];
    const query = this.shadowRoot.querySelector("input")?.value ?? "";

    const contents = this._contents
      ? `<h3>Cajón ${esc(this._contents.cajon ?? "?")}</h3>
         ${this._contents.piezas?.length ? this._rowsHtml(this._contents.piezas, false) : `<p class="empty">${esc(this._contents.frase ?? "Vacío.")}</p>`}`
      : "";
    const lowParts = low?.attributes?.piezas ?? [];
    const lowHtml = lowParts.length
      ? `<h3>Existencias bajas (${lowParts.length})</h3><ul class="rows">${lowParts
          .map((p) => `<li><span class="name">${esc(p.pieza)}</span><span class="where">${esc((p.cajones || []).join(", ") || "—")}</span>
            <span class="qty low">${fmtQty(p.total)} / ${fmtQty(p.minimo)}</span></li>`)
          .join("")}</ul>`
      : "";

    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <ha-card header="${esc(this._config?.title ?? "Cajonera")}">
        ${map ? "" : `<p class="low">Falta ${MAP}: ¿está configurada la integración cajonera?</p>`}
        <form class="search">
          <input type="search" placeholder="Buscar pieza…" value="${esc(query)}" aria-label="Buscar pieza">
          <button type="submit" ${this._busy ? "disabled" : ""}>Buscar</button>
        </form>
        ${this._results ? `<p class="phrase">${esc(this._results.frase)}</p>${this._rowsHtml(this._results.resultados, true)}` : ""}
        ${this._gridHtml(map?.attributes?.cajones)}
        ${contents}
        ${lowHtml}
        <div class="actions">
          <button class="secondary" data-action="redescubrir">Redescubrir cajonera</button>
          <button class="secondary" data-action="sincronizar_ubicaciones">Sincronizar ubicaciones</button>
        </div>
      </ha-card>`;
    this._bind();
  }

  _bind() {
    const root = this.shadowRoot;
    root.querySelector("form").addEventListener("submit", (e) => {
      e.preventDefault();
      const text = root.querySelector("input").value.trim();
      if (text) this._search(text);
    });
    root.querySelectorAll("[data-addr]").forEach((el) => {
      const open = () => this._openDrawer(Number(el.dataset.addr));
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), open()));
    });
    root.querySelectorAll("[data-withdraw]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const stock = Number(btn.dataset.withdraw);
        let qty = root.querySelector(`select[data-qty="${stock}"]`).value;
        if (qty === "other") qty = window.prompt("¿Cuántas has cogido?", "1");
        const n = Number(String(qty ?? "").replace(",", "."));
        if (!Number.isFinite(n) || n <= 0) return;
        this._withdraw(stock, n);
      }),
    );
    root.querySelectorAll("[data-action]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const res = await this._call(btn.dataset.action);
        const avisos = res?.avisos ?? [];
        this._toast(avisos.length ? avisos.join(" ") : "Todo en orden.");
      }),
    );
  }
}

customElements.define("cajonera-card", CajoneraCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "cajonera-card", name: "Cajonera", description: "Cajonera modular con InvenTree" });
