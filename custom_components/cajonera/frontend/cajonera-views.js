// Presentational half of cajonera-card: styles, string helpers and the HTML of
// every screen. The methods below are mixed into the card's prototype, so `this`
// is the card; they only read its state and never call services.

// Same version query as the entry module (see cajonera-card.js): one instance of layout.
const V = new URL(import.meta.url).search;
const { fmtQty, gridCells, panelModel, rowLabel } = await import(`./cajonera-layout.js${V}`);

export const MAP = "sensor.cajonera_mapa";
export const LOW = "sensor.cajonera_existencias_bajas";
export const NEW_CATEGORY = "__nueva";
const ACTIONS = [
  ["anadir", "Añadir"],
  ["retirar", "Retirar"],
  ["actualizar", "Actualizar a"],
];

// The quantity field and the pending flag of a Retirar button: one per part per drawer.
export const withdrawKey = (pieza_pk, cajon) => `r_${Number(pieza_pk)}_${cajon}`;

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Natural order of drawer names: A1, A2, ..., A10, B1, ..., Z1, AA1.
const nameKey = (n) => {
  const m = /^([A-Za-z]+)(\d+)$/.exec(String(n ?? ""));
  return m ? [m[1].length, m[1].toUpperCase(), Number(m[2])] : [99, String(n ?? ""), 0];
};
export const byName = (a, b) => {
  const [x, y] = [nameKey(a), nameKey(b)];
  return x[0] - y[0] || (x[1] < y[1] ? -1 : x[1] > y[1] ? 1 : 0) || x[2] - y[2];
};

// One column everywhere: on a phone (viewport under 600 px) or in a very narrow card.
const NARROW = `
  .f { grid-template-columns: 1fr; gap: 4px; }
  .two { grid-template-columns: 1fr; }
  .move { flex-direction: column; align-items: stretch; }
  .move select, .move input { flex: none; width: 100%; }
  .move .arrow { display: none; }
`;

export const STYLE = `
  :host { display: block; }
  ha-card { padding: 16px; }
  .wrap { container-type: inline-size; }
  button, input, select { font: inherit; min-height: 44px; box-sizing: border-box; }
  input, select { min-width: 0; padding: 8px 10px; border-radius: 8px; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color); width: 100%; }
  button { padding: 8px 14px; border-radius: 8px; border: 0; cursor: pointer;
    background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.secondary { background: var(--secondary-background-color); color: var(--primary-text-color); }
  button.link { background: none; color: var(--primary-text-color); padding: 0; text-align: left; font-weight: 500;
    flex: 1 1 140px; min-width: 0; }
  button:disabled { opacity: .5; cursor: default; }
  button:focus-visible, input:focus-visible, select:focus-visible, .cell:focus-visible {
    outline: 2px solid var(--primary-color); outline-offset: 2px; }
  h2 { margin: 8px 0 2px; font-size: 1.25em; }
  h3 { margin: 16px 0 4px; font-size: 1em; }
  .bar { display: flex; justify-content: space-between; gap: 8px; }
  .meta, .phrase, .hint { color: var(--secondary-text-color); margin: 4px 0; }
  .desc { margin: 4px 0; }
  .search { display: flex; gap: 8px; }
  .search input, .newcat input { flex: 1 1 0; width: auto; }
  .sugs { list-style: none; margin: 4px 0 0; padding: 0; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .sugs li { display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 6px 10px; cursor: pointer;
    border-bottom: 1px solid var(--divider-color); }
  .sugs li:last-child { border-bottom: 0; }
  .sugs li.hi { background: var(--secondary-background-color); box-shadow: inset 3px 0 0 var(--primary-color); }
  .sugs .name { flex: 1; min-width: 0; }
  .sugs .where { color: var(--secondary-text-color); white-space: nowrap; }
  .sugs .crear { color: var(--primary-color); font-weight: 600; }
  .rows { list-style: none; margin: 8px 0 0; padding: 0; }
  .rows li { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; min-height: 44px; padding: 2px 0;
    border-bottom: 1px solid var(--divider-color); }
  .rows .name { flex: 1 1 140px; min-width: 0; }
  .rows .where { font-weight: 600; min-width: 2.5em; }
  .rows .qty { min-width: 4em; text-align: right; font-variant-numeric: tabular-nums; }
  .rows .dots { flex: 1; border-bottom: 1px dotted var(--divider-color); align-self: center; }
  .rows input { width: 5em; flex: 0 0 5em; }
  .low { color: var(--error-color, #db4437); font-weight: 600; }
  .empty { color: var(--secondary-text-color); }
  .seg { display: flex; margin: 16px 0 8px; border-radius: 8px; overflow: hidden; border: 1px solid var(--primary-color); }
  .seg button { flex: 1; border-radius: 0; background: none; color: var(--primary-text-color); padding: 8px 4px; }
  .seg button.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .two label, .f { display: grid; gap: 8px; align-items: center; }
  .two label { grid-template-columns: auto 1fr; }
  .f { grid-template-columns: 8.5em 1fr; margin: 8px 0; }
  .block { width: 100%; margin-top: 8px; }
  .move { display: flex; gap: 8px; align-items: center; margin-top: 16px; }
  .move .lbl { font-weight: 600; }
  .move select, .move input { flex: 1 1 0; width: auto; }
  .newcat { display: flex; gap: 8px; margin: 0 0 8px; }
  .exists { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0;
    color: var(--warning-color, #ffa600); font-weight: 600; }
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
  .cell.guardar .lamp { animation: breathe 2.5s ease-in-out infinite; }
  .cell.seleccionado .lamp { opacity: .8; box-shadow: inset 0 0 14px 2px rgba(255, 255, 255, .55); } /* steady glow, no blink */
  @keyframes blink { 50% { opacity: .1; } }
  @keyframes alt { 0%, 49% { background: rgb(0, 0, 255); } 50%, 100% { background: rgb(255, 120, 0); } }
  @keyframes breathe { 0%, 100% { opacity: .15; } 50% { opacity: .8; } }
  .base { font-size: .7em; font-weight: 400; }
  .unplaced { color: var(--secondary-text-color); font-size: .9em; margin-top: 8px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
  @media (max-width: 600px) { ${NARROW} }
  @container (max-width: 400px) { ${NARROW} }
`;

export const viewMethods = {
  _warnHtml() {
    return this._hass?.states[MAP] ? "" : `<p class="low">Falta ${MAP}: ¿está configurada la integración cajonera?</p>`;
  },

  _input(f, { label, mode = "text", placeholder = "", required = false } = {}) {
    const im = mode === "decimal" ? ` inputmode="decimal"` : "";
    return `<label class="f"><span>${esc(label)}</span>
      <input data-f="${f}" type="text"${im} autocomplete="off" value="${esc(this._form[f] ?? "")}"
        placeholder="${esc(placeholder)}" ${required ? "required" : ""}></label>`;
  },

  _backBar(extra = "") {
    return `<div class="bar"><button type="button" class="secondary" data-act="volver">← Volver</button>${extra}</div>`;
  },

  // Search screen

  _searchHtml() {
    const q = this._form.q ?? "";
    return `
      <form class="search" data-form="buscar" role="search">
        <input data-f="q" type="search" inputmode="search" enterkeyhint="search" autocomplete="off" autocapitalize="off"
          spellcheck="false" placeholder="Buscar pieza…" aria-label="Buscar pieza" value="${esc(q)}"
          role="combobox" aria-autocomplete="list" aria-controls="sugs" aria-expanded="${this._sugs.length > 0}">
        <button type="submit" ${this._dis("buscar")}>Buscar</button>
      </form>
      <div data-live="sugs">${this._sugsHtml()}</div>
      ${this._pending.has("open") ? `<p class="phrase">Abriendo ficha…</p>` : ""}
      <div data-live="grid">${this._gridHtml()}</div>
      <div data-panel>${this._panelHtml()}</div>
      <div data-live="low">${this._lowHtml()}</div>
      <div class="actions">
        <button type="button" class="secondary" data-act="mantenimiento" data-service="redescubrir" ${this._dis("redescubrir")}>Redescubrir cajonera</button>
        <button type="button" class="secondary" data-act="mantenimiento" data-service="sincronizar_ubicaciones" ${this._dis("sincronizar_ubicaciones")}>Sincronizar ubicaciones</button>
      </div>`;
  },

  // The one panel below the grid: the last action wins (search results or the tapped drawer's contents).
  // It sits below the grid so the grid never shifts under the finger. Not a [data-live] region: only _render() redraws it.
  _panelHtml() {
    const m = panelModel(this._panel);
    if (!m) return "";
    const back =
      this._panel.tipo === "cajon" && this._lastSearch
        ? `<button type="button" class="link" data-act="volver_resultados">← Volver a los resultados</button>`
        : "";
    const phrase = m.phrase ? `<p class="${this._panel.tipo === "cajon" ? "empty" : "phrase"}">${esc(m.phrase)}</p>` : "";
    return `<h3>${esc(m.title)}</h3>${back}${phrase}${this._partRows(m.rows, m.showWhere, true)}`;
  },

  _sugsHtml() {
    if (!this._sugs.length) return "";
    const items = this._sugs
      .map((r, i) => {
        const hi = i === this._hi;
        const attrs = `id="sug-${i}" data-sug="${i}" role="option" aria-selected="${hi}" class="${hi ? "hi" : ""}"`;
        if (r.crear) return `<li ${attrs}><span class="name crear">+ Crear «${esc(r.nombre)}»</span></li>`;
        const where = r.cajones?.length ? r.cajones.join(", ") : "sin cajón";
        return `<li ${attrs}><span class="name">${esc(r.nombre)}${r.ipn ? ` <small>· ${esc(r.ipn)}</small>` : ""}</span>
          <span class="where">${esc(where)} · ${fmtQty(r.total)} uds</span></li>`;
      })
      .join("");
    return `<ul class="sugs" id="sugs" role="listbox" aria-label="Sugerencias">${items}</ul>`;
  },

  _partRows(rows, showWhere, withdraw = false) {
    if (!rows?.length) return "";
    return `<ul class="rows">${rows
      .map((r) => {
        const key = withdrawKey(r.pieza_pk, r.cajon);
        const picker = withdraw
          ? `<input data-f="${esc(key)}" type="text" inputmode="decimal" value="${esc(this._form[key] ?? "1")}" aria-label="Cantidad a retirar">
             <button type="button" data-act="retirar_stock" data-pk="${Number(r.pieza_pk)}" data-cajon="${esc(r.cajon)}" ${this._dis(key)}>Retirar</button>`
          : "";
        return `<li>
          <button type="button" class="link name" data-act="pieza" data-pk="${Number(r.pieza_pk)}">${esc(r.pieza)}</button>
          ${showWhere ? `<span class="where">${esc(r.cajon)}</span>` : ""}
          <span class="qty">${fmtQty(r.cantidad)} uds</span>
          ${r.bajo ? `<span class="low">⚠ bajo</span>` : ""}
          ${picker}
        </li>`;
      })
      .join("")}</ul>`;
  },

  _lowHtml() {
    const parts = this._hass?.states[LOW]?.attributes?.piezas ?? [];
    if (!parts.length) return "";
    return `<h3>Existencias bajas (${parts.length})</h3><ul class="rows">${parts
      .map(
        (p) => `<li><button type="button" class="link name" data-act="pieza" data-pk="${Number(p.pieza_pk)}">${esc(p.pieza)}</button>
          <span class="where">${esc((p.cajones || []).join(", ") || "—")}</span>
          <span class="qty low">${fmtQty(p.total)} / ${fmtQty(p.minimo)}</span></li>`,
      )
      .join("")}</ul>`;
  },

  _gridHtml() {
    const g = gridCells(this._mapDrawers());
    if (!g.cells.length) return `<p class="empty">No hay cajones todavía.</p>`;
    const marked = this._screen === "crear" ? this._target?.direccion : this._selected;
    const rowLabels = Array.from({ length: g.rows }, (_, i) => `<div>${rowLabel(g.rows - 1 - i)}</div>`).join("");
    const colLabels = Array.from({ length: g.cols }, (_, i) => `<div>${i + 1}</div>`).join("");
    const cells = g.cells
      .map((c) => {
        const addr = Number(c.direccion);
        const cls = ["cell", c.modo, c.bajo && "bajo", c.error && "error", c.colSpan > 1 && "wide", marked === addr && "selected"]
          .filter(Boolean)
          .map(esc)
          .join(" ");
        const lamp = c.rgb.some((v) => v > 0)
          ? `<div class="lamp" style="background: rgb(${c.rgb.map(Number).join(",")})"></div>`
          : "";
        return `<div class="${cls}" data-addr="${addr}" role="button" tabindex="0"
            style="grid-column: ${c.col} / span ${c.colSpan}; grid-row: ${c.row} / span ${c.rowSpan}"
            title="${esc(c.nombre ?? "")} · dirección ${addr}${c.error ? " · " + esc(c.error) : ""}">
            ${lamp}<span>${esc(c.nombre ?? "?")}</span>${addr === 0 ? `<span class="base">base</span>` : ""}
          </div>`;
      })
      .join("");
    const unplaced = g.unplaced.length
      ? `<p class="unplaced">Sin posición: ${g.unplaced.map((c) => `cajón ${Number(c.direccion)}${c.error ? ` (${esc(c.error)})` : ""}`).join(", ")}</p>`
      : "";
    return `<div class="gridwrap">
        <div class="rowlabels" style="grid-template-rows: repeat(${g.rows}, 1fr)">${rowLabels}</div>
        <div class="grid" style="grid-template-columns: repeat(${g.cols}, 1fr); grid-template-rows: repeat(${g.rows}, auto)">${cells}</div>
        <div class="collabels" style="grid-template-columns: repeat(${g.cols}, 1fr)">${colLabels}</div>
      </div>${unplaced}`;
  },

  // Part sheet

  // A <select> of drawers: the part's own first (with quantities), then the rest of the cabinet.
  _drawerSelect(f, { partOnly = false, exclude = "", label }) {
    const own = this._sheet?.cajones ?? [];
    const ownSet = new Set(own.map((r) => String(r.direccion)));
    const mine = own.filter((r) => String(r.direccion) !== exclude);
    const others = partOnly
      ? []
      : this._namedDrawers().filter((d) => !ownSet.has(String(d.direccion)) && String(d.direccion) !== exclude);
    const valid = new Set([...mine.map((r) => String(r.direccion)), ...others.map((d) => String(d.direccion))]);
    // A value the options no longer offer would show one drawer and send another.
    if (!valid.has(this._form[f] ?? "")) this._form[f] = "";
    const value = this._form[f];
    const opt = (v, text) => `<option value="${Number(v)}" ${String(v) === value ? "selected" : ""}>${esc(text)}</option>`;
    const mineHtml = mine.map((r) => opt(r.direccion, `${r.cajon} · ${fmtQty(r.cantidad)} uds`)).join("");
    const othersHtml = others.map((d) => opt(d.direccion, d.nombre)).join("");
    const placeholder = value === "" ? `<option value="" selected disabled>Elige cajón…</option>` : "";
    const rest = othersHtml && mineHtml ? `<optgroup label="Otros cajones">${othersHtml}</optgroup>` : othersHtml;
    return `<select data-f="${f}" aria-label="${esc(label)}">${placeholder}${mineHtml}${rest}</select>`;
  },

  _sheetHtml() {
    const s = this._sheet;
    if (!s) return this._searchHtml();
    const f = this._form;
    const ruta = this._categoryPath(s.categoria_pk);
    const meta = [ruta && esc(ruta), s.ipn && `IPN ${esc(s.ipn)}`].filter(Boolean).join(" · ");
    const rows = s.cajones?.length
      ? `<ul class="rows">${s.cajones
          .map((r) => `<li><span class="where">${esc(r.cajon)}</span><span class="dots"></span><span class="qty">${fmtQty(r.cantidad)} uds</span></li>`)
          .join("")}</ul>`
      : `<p class="empty">Sin cajón.</p>`;
    const fuera = Number(s.fuera) > 0 ? `<p class="meta">Fuera de la cajonera: ${fmtQty(s.fuera)} uds</p>` : "";
    const low = s.bajo ? `<p class="low">⚠ Por debajo del mínimo: ${fmtQty(s.total)} / ${fmtQty(s.minimo)}</p>` : "";
    const seg = ACTIONS.map(
      ([v, text]) =>
        `<button type="button" role="radio" aria-checked="${f.accion === v}" class="${f.accion === v ? "on" : ""}"
          data-act="accion" data-v="${v}">${text}</button>`,
    ).join("");
    const move = s.cajones?.length
      ? `<form class="move" data-form="mover">
          <span class="lbl">Mover:</span>
          ${this._drawerSelect("desde", { partOnly: true, label: "Mover desde" })}
          <span class="arrow" aria-hidden="true">→</span>
          ${this._drawerSelect("hasta", { exclude: f.desde, label: "Mover a" })}
          <input data-f="mover_cantidad" type="text" inputmode="decimal" autocomplete="off" value="${esc(f.mover_cantidad ?? "")}"
            placeholder="todo" aria-label="Cantidad a mover (vacío: todo)">
          <button type="submit" ${this._dis("mover")}>Mover</button>
        </form>`
      : "";
    return `
      ${this._backBar(`<button type="button" class="secondary" data-act="editar">Editar</button>`)}
      <h2>${esc(s.nombre)}</h2>
      ${meta ? `<p class="meta">${meta}</p>` : ""}
      ${s.descripcion ? `<p class="desc">${esc(s.descripcion)}</p>` : ""}
      ${low}
      ${rows}
      ${fuera}
      <form data-form="confirmar">
        <div class="seg" role="radiogroup" aria-label="Acción">${seg}</div>
        <div class="two">
          <label><span>Cajón</span>${this._drawerSelect("cajon", { partOnly: f.accion === "retirar", label: "Cajón" })}</label>
          <label><span>Cantidad</span><input data-f="cantidad" type="text" inputmode="decimal" autocomplete="off"
            value="${esc(f.cantidad ?? "")}" aria-label="Cantidad"></label>
        </div>
        <button type="submit" class="block" ${this._dis("confirmar")}>Confirmar</button>
      </form>
      ${move}`;
  },

  // Create and edit

  _categoryField(prefix) {
    const f = `${prefix}categoria`;
    const value = this._form[f] ?? "";
    const cats = this._categories;
    const known = cats?.some((c) => String(c.pk) === value);
    const opt = (v, text) => `<option value="${esc(v)}" ${String(v) === value ? "selected" : ""}>${esc(text)}</option>`;
    let options = value === "" ? `<option value="" selected disabled>Elige una categoría…</option>` : "";
    if (cats === null) options += `<option value="" disabled>Cargando categorías…</option>`;
    if (value && value !== NEW_CATEGORY && !known) options += opt(value, "Categoría actual");
    options += (cats ?? []).map((c) => opt(c.pk, c.ruta)).join("");
    options += opt(NEW_CATEGORY, "+ Nueva categoría…");
    const inline =
      value === NEW_CATEGORY
        ? `<div class="newcat">
            <input data-f="${prefix}nueva" type="text" autocomplete="off" value="${esc(this._form[`${prefix}nueva`] ?? "")}"
              placeholder="Nombre de la categoría" aria-label="Nombre de la nueva categoría">
            <button type="button" data-act="cat" data-p="${prefix}" ${this._dis(`${prefix}cat`)}>Crear categoría</button>
          </div>`
        : "";
    return `<label class="f"><span>Categoría</span><select data-f="${f}" aria-label="Categoría">${options}</select></label>${inline}`;
  },

  _createHtml() {
    const t = this._target;
    const name = t ? this._drawerName(t.direccion) ?? `dirección ${t.direccion}` : null;
    const drawer = t
      ? `<b>${esc(name)}</b> (${t.origen === "sugerido" ? "sugerido" : "elegido"}) · toca otro para cambiarlo`
      : "Toca un cajón en la cuadrícula";
    const exists = this._exists
      ? `<p class="exists">${esc(this._exists.frase)} <button type="button" data-act="abrir" ${this._dis("open")}>Abrir</button></p>`
      : "";
    return `
      ${this._backBar()}
      <h2>Nueva pieza</h2>
      <form data-form="crear">
        ${this._input("c_nombre", { label: "Nombre", required: true })}
        ${this._categoryField("c_")}
        <div class="f"><span>Cajón</span><span>${drawer}${this._hint ? `<br><small class="hint">${esc(this._hint)}</small>` : ""}</span></div>
        ${this._input("c_cantidad", { label: "Cantidad", mode: "decimal", required: true })}
        ${this._input("c_ipn", { label: "Ref./IPN", placeholder: "opcional" })}
        ${this._input("c_minimo", { label: "Mínimo", mode: "decimal", placeholder: "opcional" })}
        ${this._input("c_palabras", { label: "Palabras clave", placeholder: "opcional" })}
        ${this._input("c_descripcion", { label: "Descripción", placeholder: "opcional" })}
        ${exists}
        <button type="submit" class="block" ${this._dis("crear")}>Crear pieza</button>
      </form>
      <div data-live="grid">${this._gridHtml()}</div>`;
  },

  _editHtml() {
    if (!this._sheet) return this._searchHtml();
    return `
      ${this._backBar()}
      <h2>Editar pieza</h2>
      <form data-form="guardar">
        ${this._input("e_nombre", { label: "Nombre", required: true })}
        ${this._categoryField("e_")}
        ${this._input("e_ipn", { label: "Ref./IPN", placeholder: "opcional" })}
        ${this._input("e_minimo", { label: "Mínimo", mode: "decimal", placeholder: "sin mínimo" })}
        ${this._input("e_palabras", { label: "Palabras clave", placeholder: "opcional" })}
        ${this._input("e_descripcion", { label: "Descripción", placeholder: "opcional" })}
        <button type="submit" class="block" ${this._dis("guardar")}>Guardar</button>
      </form>`;
  },
};
