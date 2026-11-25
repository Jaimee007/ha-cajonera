// cajonera-card: Lovelace card for the modular drawer cabinet.
// Four screens, one visible at a time (spec 2026-11-24-inventory-card-design.md
// section 7.3): search (autocomplete, results, drawer grid, drawer contents,
// low stock), part sheet (add / withdraw / count / move), create and edit.
// Talks only to the cajonera.* services.
//
// Install: nothing to copy. The integration serves this folder itself and loads
// the card on every dashboard (no /config/www copy, no manual resource). Add it
// to a dashboard with
//   type: custom:cajonera-card

import { createSequencer, debounceMs, defaultDrawer, fmtQty, minChars, parseQty, suggestionRows } from "./cajonera-layout.js";
import { LOW, MAP, NEW_CATEGORY, STYLE, byName, esc, viewMethods } from "./cajonera-views.js";

const BAD_QTY = "Dime una cantidad válida.";
// Services that change InvenTree: when the call itself fails we cannot tell whether it was applied.
const WRITES = new Set([
  "inventario_retirar",
  "inventario_anadir",
  "inventario_actualizar",
  "inventario_mover",
  "pieza_crear",
  "pieza_editar",
  "categoria_crear",
]);
const MAYBE_WRITTEN = "No sé si se ha registrado. Revisa antes de repetirlo.";

class CajoneraCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._screen = "buscar"; // "buscar" | "ficha" | "crear" | "editar"
    this._stamp = "";
    this._rendered = false;
    this._pending = new Set(); // keys of the write buttons whose call is in flight
    // Search
    this._seq = createSequencer();
    this._timer = null;
    this._sugs = []; // rows from suggestionRows()
    this._hi = -1; // highlighted suggestion
    this._litBySuggest = false; // the cabinet shows drawers lit by the suggestions (iluminar_busqueda)
    this._results = null; // { frase, resultados } from inventario_buscar
    this._selected = null; // drawer address whose contents are shown
    this._contents = null; // { cajon, piezas }
    // Part sheet, create and edit
    this._sheet = null; // answer of `pieza`
    this._categories = null; // [{ pk, nombre, ruta }], null until loaded
    this._loadingCategories = false;
    this._catSeq = createSequencer();
    this._target = null; // create screen: { direccion, origen: "sugerido" | "elegido" }
    this._hint = null; // create screen: `motivo` of sugerir_cajon
    this._exists = null; // create screen: { pk, frase } after "Ya existe"
    // Everything typed survives re-renders: one value per [data-f] field, as strings.
    this._form = { q: "" };
    this._focus = null; // { f, start, end } of the focused field
    this._wantFocus = null; // a field to focus on the next render
    this._prefilled = null; // { text, value } put into "cantidad" by "Actualizar a"

    // Event delegation on the shadow root: it outlives every innerHTML rebuild.
    const root = this.shadowRoot;
    root.addEventListener("click", (e) => this._onClick(e));
    root.addEventListener("input", (e) => this._onInput(e));
    root.addEventListener("change", (e) => this._onChange(e));
    root.addEventListener("keydown", (e) => this._onKey(e));
    root.addEventListener("submit", (e) => this._onSubmit(e));
  }

  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 8;
  }

  disconnectedCallback() {
    clearTimeout(this._timer);
  }

  set hass(hass) {
    this._hass = hass;
    const stamp = [MAP, LOW].map((id) => hass.states[id]?.last_updated ?? "").join("|");
    if (stamp === this._stamp) return;
    this._stamp = stamp;
    // Sensor updates only touch the grid and the low-stock list: inputs keep focus untouched.
    if (this._rendered) this._renderLive();
    else this._render();
  }

  // Services

  async _call(service, data = {}, key = null) {
    if (key) {
      this._pending.add(key);
      this._render();
    }
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
      this._toast(WRITES.has(service) ? MAYBE_WRITTEN : `Error: ${err?.message ?? err}`);
      return null;
    } finally {
      if (key) {
        this._pending.delete(key);
        this._render();
      }
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
    if (!message) return;
    this.dispatchEvent(new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
  }

  _dis(key) {
    return this._pending.has(key) ? "disabled" : "";
  }

  // Map helpers

  _mapDrawers() {
    return this._hass?.states[MAP]?.attributes?.cajones ?? [];
  }

  _namedDrawers() {
    return this._mapDrawers()
      .filter((c) => c.nombre && !c.error)
      .map((c) => ({ direccion: c.direccion, nombre: c.nombre }))
      .sort((a, b) => byName(a.nombre, b.nombre));
  }

  _drawerName(address) {
    const own = this._sheet?.cajones?.find((r) => r.direccion === address);
    if (own) return own.cajon;
    return this._mapDrawers().find((c) => c.direccion === address && !c.error)?.nombre ?? null;
  }

  // Search screen

  _onQuery(value) {
    this._form.q = value;
    this._highlight(-1); // the rows on screen answer older text
    clearTimeout(this._timer);
    this._seq.next(); // whatever is in flight answers older text
    if (value.trim().length < minChars) {
      this._clearSuggestLight();
      if (this._sugs.length) {
        this._sugs = [];
        this._renderSuggestions();
      }
      return;
    }
    this._timer = setTimeout(() => this._suggest(value.trim()), debounceMs);
  }

  async _suggest(texto) {
    const n = this._seq.next();
    const res = await this._call("sugerir", { texto });
    if (!this._seq.accept(n) || this._screen !== "buscar" || !res) return;
    this._sugs = suggestionRows(res.sugerencias, texto);
    this._hi = -1;
    this._lightSuggestions();
    this._renderSuggestions();
  }

  // The drawers of the suggestions on screen light up while typing. The card lights them itself, and only
  // when it accepts an answer, so a late answer can never light drawers for text the user moved on from.
  _lightSuggestions() {
    const rows = this._sugs.filter((r) => !r.crear);
    const direcciones = [...new Set(rows.flatMap((r) => r.direcciones ?? []))];
    const bajos = [...new Set(rows.filter((r) => r.bajo).flatMap((r) => r.direcciones ?? []))];
    if (!direcciones.length) return this._clearSuggestLight();
    this._litBySuggest = true;
    this._callNoResponse("iluminar_busqueda", { direcciones, bajos });
  }

  // Put the suggestion lights out, but only if they are lit: opening a sheet or running the full search
  // relights by itself, and a clear would only make the cabinet flicker.
  _clearSuggestLight() {
    if (!this._litBySuggest) return;
    this._litBySuggest = false;
    this._callNoResponse("iluminar_busqueda", { direcciones: [], bajos: [] });
  }

  _closeSuggestions() {
    clearTimeout(this._timer);
    this._seq.next();
    this._sugs = [];
    this._hi = -1;
  }

  _highlight(i) {
    this._hi = i;
    const root = this.shadowRoot;
    root.querySelectorAll("[data-sug]").forEach((li) => {
      const on = Number(li.dataset.sug) === i;
      li.classList.toggle("hi", on);
      li.setAttribute("aria-selected", String(on));
    });
    const input = root.querySelector('[data-f="q"]');
    if (input) {
      if (i >= 0) input.setAttribute("aria-activedescendant", `sug-${i}`);
      else input.removeAttribute("aria-activedescendant");
    }
  }

  _onQueryKey(e) {
    const n = this._sugs.length;
    if (e.key === "ArrowDown" && n) {
      e.preventDefault();
      this._highlight(Math.min(this._hi + 1, n - 1));
    } else if (e.key === "ArrowUp" && n) {
      e.preventDefault();
      this._highlight(Math.max(this._hi - 1, -1));
    } else if (e.key === "Enter" && this._hi >= 0 && this._hi < n) {
      e.preventDefault(); // a highlighted row wins over the full search
      this._choose(this._hi);
    } else if (e.key === "Escape" && n) {
      e.preventDefault();
      this._closeSuggestions();
      this._clearSuggestLight();
      this._renderSuggestions();
    }
  }

  _choose(i) {
    const row = this._sugs[i];
    if (!row) return;
    if (row.crear) this._startCreate(row.nombre);
    else this._openPart(row.pieza_pk);
  }

  async _search(texto) {
    this._closeSuggestions();
    this._litBySuggest = false; // inventario_buscar lights the drawers by itself
    const res = await this._call("inventario_buscar", { texto, origen: "panel" }, "buscar");
    if (!res) return;
    this._results = res;
    this._toast(res.frase);
    this._render();
  }

  async _openPart(pk) {
    if (this._pending.has("open")) return;
    clearTimeout(this._timer);
    this._seq.next(); // a late suggestions answer must not light drawers over the sheet's
    const res = await this._call("pieza", { pieza_pk: pk }, "open");
    if (!res) return;
    if (res.pieza_pk == null) {
      this._toast(res.frase ?? "No encuentro esa pieza.");
      return;
    }
    this._showSheet(res);
  }

  async _openDrawer(address, identify = true) {
    this._selected = address;
    if (identify) this._callNoResponse("identificar", { direccion: address });
    const res = await this._call("contenido_cajon", { direccion: address });
    this._contents = res;
    this._render();
  }

  async _withdrawStock(stock) {
    const key = `r_${stock}`;
    if (this._pending.has(key)) return;
    const cantidad = parseQty(this._form[key] ?? "1");
    if (cantidad === null) return this._toast(BAD_QTY);
    const res = await this._call("inventario_retirar", { stock, cantidad, origen: "panel" }, key);
    if (!res) return;
    this._toast(res.frase);
    if (!res.hecho) return;
    if (this._results?.resultados) {
      this._results.resultados = this._results.resultados.map((r) =>
        r.stock === stock ? { ...r, cantidad: r.cantidad - cantidad } : r,
      );
    }
    if (this._selected !== null) await this._openDrawer(this._selected, false);
    this._render();
  }

  async _runMaintenance(service) {
    if (this._pending.has(service)) return;
    const res = await this._call(service, {}, service);
    if (!res) return;
    const avisos = res.avisos ?? [];
    this._toast(avisos.length ? avisos.join(" ") : "Todo en orden.");
  }

  _onCell(address) {
    if (this._screen === "crear") {
      if (!this._drawerName(address)) return this._toast("Ese cajón no tiene posición válida.");
      this._catSeq.next(); // a pending suggestion must not override the user's tap
      this._target = { direccion: address, origen: "elegido" };
      this._hint = null;
      // The cabinet must light the drawer the card targets, not the one it suggested.
      this._callNoResponse("guardar_aqui", { direccion: address });
      this._render();
    } else {
      this._openDrawer(address);
    }
  }

  // Part sheet

  // The create screen owns the magenta "store here" light: put it out when leaving.
  _leaveCreate() {
    if (this._screen !== "crear") return;
    this._catSeq.next(); // a late suggestion must not light a drawer on another screen
    if (this._target) this._callNoResponse("guardar_apagar");
    this._target = null;
  }

  _showSheet(sheet) {
    this._leaveCreate();
    this._closeSuggestions();
    this._litBySuggest = false; // pieza lit the part's drawers by itself
    this._sheet = sheet;
    this._screen = "ficha";
    const d = defaultDrawer(sheet.cajones);
    Object.assign(this._form, {
      accion: "anadir",
      cajon: d === null ? "" : String(d),
      cantidad: "",
      desde: d === null ? "" : String(d),
      hasta: "",
      mover_cantidad: "",
    });
    this._loadCategories();
    this._render();
  }

  _qtyIn(address) {
    const row = this._sheet?.cajones?.find((r) => String(r.direccion) === String(address));
    return row ? Number(row.cantidad) || 0 : 0;
  }

  _setAction(accion) {
    const f = this._form;
    const was = f.accion;
    f.accion = accion;
    const own = (this._sheet?.cajones ?? []).map((r) => String(r.direccion));
    if (accion === "retirar" && !own.includes(f.cajon)) {
      const d = defaultDrawer(this._sheet?.cajones);
      f.cajon = d === null ? "" : String(d);
    }
    if (accion === "actualizar") this._prefill(f.cajon);
    else if (was === "actualizar") f.cantidad = "";
    this._render();
  }

  // "Actualizar a" starts from the drawer's quantity. The number is kept too: fmtQty may give
  // a text such as "1,125" that parseQty refuses as ambiguous, and an untouched prefill must still work.
  _prefill(address) {
    const value = address === "" ? null : this._qtyIn(address);
    this._form.cantidad = value === null ? "" : fmtQty(value);
    this._prefilled = value === null ? null : { text: this._form.cantidad, value };
  }

  // Apply a confirmed write to the sheet locally: re-reading it would light the drawers again.
  _applyStock(address, fn) {
    const s = this._sheet;
    const rows = s.cajones.map((r) => ({ ...r }));
    let row = rows.find((r) => r.direccion === address);
    if (!row) {
      const name = this._drawerName(address);
      if (!name || fn(0) <= 0) return; // counting an empty drawer to 0 creates nothing
      row = { cajon: name, direccion: address, cantidad: 0 };
      rows.push(row);
    }
    const before = Number(row.cantidad) || 0;
    row.cantidad = Math.max(0, fn(before));
    const total = Number(s.total || 0) + row.cantidad - before;
    rows.sort((a, b) => b.cantidad - a.cantidad || byName(a.cajon, b.cajon));
    this._sheet = { ...s, cajones: rows, total, bajo: Number(s.minimo) > 0 && total < Number(s.minimo) };
  }

  async _confirm() {
    if (this._pending.has("confirmar")) return;
    const s = this._sheet;
    const f = this._form;
    const accion = f.accion;
    if (f.cajon === "") return this._toast("Elige un cajón.");
    const direccion = Number(f.cajon);
    const untouched = accion === "actualizar" && this._prefilled && f.cantidad === this._prefilled.text;
    const qty = untouched ? this._prefilled.value : parseQty(f.cantidad, { allowZero: accion === "actualizar" });
    if (qty === null) return this._toast(BAD_QTY);
    let res;
    if (accion === "retirar") {
      const row = s.cajones.find((r) => r.direccion === direccion);
      if (!row) return this._toast(`No hay ${s.nombre} en ese cajón.`);
      // The part id plus an explicit drawer: no name lookup, no voice context.
      res = await this._call(
        "inventario_retirar",
        { pieza_pk: s.pieza_pk, cantidad: qty, cajon: row.cajon, origen: "panel" },
        "confirmar",
      );
    } else {
      const service = accion === "anadir" ? "inventario_anadir" : "inventario_actualizar";
      res = await this._call(service, { pieza_pk: s.pieza_pk, direccion, cantidad: qty }, "confirmar");
    }
    if (!res) return;
    this._toast(res.frase);
    if (!res.hecho || this._sheet?.pieza_pk !== s.pieza_pk) return;
    this._applyStock(direccion, (q) => (accion === "anadir" ? q + qty : accion === "retirar" ? q - qty : qty));
    if (accion === "actualizar") this._prefill(f.cajon);
    else f.cantidad = "";
    this._render();
  }

  async _move() {
    if (this._pending.has("mover")) return;
    const s = this._sheet;
    const f = this._form;
    if (f.desde === "" || f.hasta === "") return this._toast("Elige el cajón de origen y el de destino.");
    const desde = Number(f.desde);
    const hasta = Number(f.hasta);
    const data = { pieza_pk: s.pieza_pk, desde, hasta };
    const text = (f.mover_cantidad ?? "").trim();
    if (text) {
      data.cantidad = parseQty(text);
      if (data.cantidad === null) return this._toast(BAD_QTY);
    }
    const res = await this._call("inventario_mover", data, "mover");
    if (!res) return;
    this._toast(res.frase);
    if (!res.hecho || this._sheet?.pieza_pk !== s.pieza_pk) return;
    const moved = data.cantidad ?? this._qtyIn(desde);
    this._applyStock(desde, (q) => q - moved);
    this._applyStock(hasta, (q) => q + moved);
    // A move that empties the drawer takes the stock item with it: the part no longer lives there.
    if (this._qtyIn(desde) <= 0) {
      this._sheet = { ...this._sheet, cajones: this._sheet.cajones.filter((r) => r.direccion !== desde) };
      f.desde = "";
      if (String(desde) === f.cajon) f.cajon = "";
    }
    f.mover_cantidad = "";
    this._render();
  }

  // Create and edit

  async _loadCategories() {
    if (this._categories?.length || this._loadingCategories) return;
    this._loadingCategories = true;
    const res = await this._call("categorias");
    this._loadingCategories = false;
    this._categories = res?.categorias ?? [];
    this._render();
  }

  _categoryPath(pk) {
    return this._categories?.find((c) => c.pk === pk)?.ruta ?? null;
  }

  _startCreate(nombre) {
    this._closeSuggestions();
    this._clearSuggestLight();
    this._catSeq.next(); // a late answer from an earlier create form must not land on this one
    this._screen = "crear";
    Object.assign(this._form, {
      c_nombre: nombre ?? "",
      c_categoria: "",
      c_nueva: "",
      c_cantidad: "",
      c_ipn: "",
      c_minimo: "",
      c_palabras: "",
      c_descripcion: "",
    });
    this._target = null;
    this._hint = null;
    this._exists = null;
    this._wantFocus = { f: "c_categoria" };
    this._loadCategories();
    this._render();
  }

  _onCategory(prefix, value) {
    if (value === NEW_CATEGORY) {
      this._wantFocus = { f: `${prefix}nueva` };
    } else if (prefix === "c_" && value) {
      this._suggestDrawer(Number(value));
    }
    this._render();
  }

  async _suggestDrawer(categoria) {
    const n = this._catSeq.next();
    this._hint = "Buscando cajón…";
    const res = await this._call("sugerir_cajon", { categoria });
    if (!this._catSeq.accept(n) || this._screen !== "crear") return;
    this._hint = res?.motivo ?? null;
    if (res?.direccion != null) {
      this._target = { direccion: res.direccion, origen: "sugerido" };
      // The server only answers: the card lights the drawer once it accepts the answer.
      this._callNoResponse("guardar_aqui", { direccion: res.direccion });
    } else if (this._target?.origen !== "elegido") {
      // No suggestion for this category: an earlier suggestion no longer applies and its light goes out.
      // A tapped drawer stays targeted, and lit.
      this._target = null;
      this._callNoResponse("guardar_apagar");
    }
    this._render();
  }

  async _createCategory(prefix) {
    const key = `${prefix}cat`;
    if (this._pending.has(key)) return;
    const nombre = (this._form[`${prefix}nueva`] ?? "").trim();
    if (!nombre) return this._toast("Dime un nombre para la categoría.");
    const res = await this._call("categoria_crear", { nombre }, key);
    if (!res) return;
    const c = res.categoria;
    if (!c) return this._toast(res.frase ?? "No he podido crear la categoría.");
    this._categories = [...(this._categories ?? []).filter((x) => x.pk !== c.pk), c].sort((a, b) =>
      String(a.ruta).localeCompare(String(b.ruta), "es", { sensitivity: "base" }),
    );
    this._form[`${prefix}categoria`] = String(c.pk);
    this._form[`${prefix}nueva`] = "";
    this._toast(`Categoría «${c.ruta}» lista.`);
    if (prefix === "c_" && this._screen === "crear") this._suggestDrawer(c.pk);
    this._render();
  }

  async _create() {
    if (this._pending.has("crear")) return;
    const f = this._form;
    const nombre = (f.c_nombre ?? "").trim();
    if (!nombre) return this._toast("Dime un nombre para la pieza.");
    if (!f.c_categoria || f.c_categoria === NEW_CATEGORY) return this._toast("Elige una categoría.");
    if (!this._target) return this._toast("Elige un cajón: toca uno en la cuadrícula.");
    const cantidad = parseQty(f.c_cantidad);
    if (cantidad === null) return this._toast(BAD_QTY);
    const data = { nombre, categoria: Number(f.c_categoria), direccion: this._target.direccion, cantidad };
    for (const [key, field] of [
      ["ipn", "c_ipn"],
      ["palabras_clave", "c_palabras"],
      ["descripcion", "c_descripcion"],
    ]) {
      const v = (f[field] ?? "").trim();
      if (v) data[key] = v;
    }
    if ((f.c_minimo ?? "").trim()) {
      data.minimo = parseQty(f.c_minimo, { allowZero: true });
      if (data.minimo === null) return this._toast(BAD_QTY);
    }
    const res = await this._call("pieza_crear", data, "crear");
    if (!res) return;
    this._toast(res.frase);
    if (res.hecho && res.pieza_pk != null) {
      await this._openPart(res.pieza_pk);
    } else if (res.pieza_pk != null) {
      // "Ya existe …": offer the existing part instead.
      this._exists = { pk: res.pieza_pk, frase: res.frase };
      this._render();
    }
  }

  _startEdit() {
    const s = this._sheet;
    Object.assign(this._form, {
      e_nombre: s.nombre ?? "",
      e_categoria: s.categoria_pk == null ? "" : String(s.categoria_pk),
      e_nueva: "",
      e_ipn: s.ipn ?? "",
      e_minimo: this._minimoText(s),
      e_palabras: s.palabras_clave ?? "",
      e_descripcion: s.descripcion ?? "",
    });
    this._screen = "editar";
    this._loadCategories();
    this._render();
  }

  _minimoText(s) {
    return Number(s.minimo) > 0 ? fmtQty(s.minimo) : "";
  }

  async _save() {
    if (this._pending.has("guardar")) return;
    const s = this._sheet;
    const f = this._form;
    const changes = {};
    for (const [key, field] of [
      ["nombre", "e_nombre"],
      ["ipn", "e_ipn"],
      ["palabras_clave", "e_palabras"],
      ["descripcion", "e_descripcion"],
    ]) {
      const v = (f[field] ?? "").trim();
      if (v !== String(s[key] ?? "").trim()) changes[key] = v;
    }
    if ("nombre" in changes && !changes.nombre) return this._toast("Dime un nombre para la pieza.");
    if (f.e_categoria && f.e_categoria !== NEW_CATEGORY && Number(f.e_categoria) !== s.categoria_pk) {
      changes.categoria = Number(f.e_categoria);
    }
    if ((f.e_minimo ?? "") !== this._minimoText(s)) {
      const text = (f.e_minimo ?? "").trim();
      const minimo = text === "" ? 0 : parseQty(text, { allowZero: true });
      if (minimo === null) return this._toast(BAD_QTY);
      if (minimo !== Number(s.minimo || 0)) changes.minimo = minimo;
    }
    if (!Object.keys(changes).length) {
      this._toast("No hay cambios.");
      this._screen = "ficha";
      return this._render();
    }
    const res = await this._call("pieza_editar", { pieza_pk: s.pieza_pk, ...changes }, "guardar");
    if (!res) return;
    this._toast(res.frase);
    if (!res.hecho) return;
    const fresh = await this._call("pieza", { pieza_pk: s.pieza_pk }, "guardar");
    if (fresh?.pieza_pk != null) return this._showSheet(fresh);
    // Saved, but the sheet could not be re-read: show what we know.
    const { categoria, ...rest } = changes;
    this._sheet = { ...s, ...rest, ...(categoria !== undefined ? { categoria_pk: categoria } : {}) };
    this._screen = "ficha";
    this._render();
  }

  // Events (delegated)

  _onClick(e) {
    const el = e.target?.closest?.("[data-act],[data-sug],[data-addr]");
    if (!el || el.disabled) return;
    const ds = el.dataset;
    if (ds.sug !== undefined) return this._choose(Number(ds.sug));
    if (ds.addr !== undefined) return this._onCell(Number(ds.addr));
    switch (ds.act) {
      case "volver":
        this._leaveCreate();
        this._screen = this._screen === "editar" ? "ficha" : "buscar";
        return this._render();
      case "editar":
        return this._startEdit();
      case "accion":
        return this._setAction(ds.v);
      case "abrir":
        return this._exists && this._openPart(this._exists.pk);
      case "cat":
        return this._createCategory(ds.p);
      case "retirar_stock":
        return this._withdrawStock(Number(ds.stock));
      case "pieza":
        return Number.isFinite(Number(ds.pk)) ? this._openPart(Number(ds.pk)) : undefined;
      case "mantenimiento":
        return this._runMaintenance(ds.service);
      default:
        return undefined;
    }
  }

  _onInput(e) {
    const f = e.target?.dataset?.f;
    if (!f) return;
    this._form[f] = e.target.value;
    if (f === "q") this._onQuery(e.target.value);
    if (f === "c_nombre" && this._exists) {
      this._exists = null;
      this.shadowRoot.querySelector(".exists")?.remove();
    }
  }

  _onChange(e) {
    const f = e.target?.dataset?.f;
    if (!f) return;
    const value = e.target.value;
    this._form[f] = value;
    if (f === "cajon" && this._form.accion === "actualizar") {
      this._prefill(value);
      this._render();
    } else if (f === "desde") {
      if (this._form.hasta === value) this._form.hasta = "";
      this._render();
    } else if (f === "c_categoria" || f === "e_categoria") {
      this._onCategory(f.slice(0, 2), value);
    }
  }

  _onKey(e) {
    const t = e.target;
    const f = t?.dataset?.f;
    if (f === "q") return this._onQueryKey(e);
    if ((f === "c_nueva" || f === "e_nueva") && e.key === "Enter") {
      e.preventDefault(); // not the surrounding create / edit form
      return this._createCategory(f.slice(0, 2));
    }
    if (t?.dataset?.addr !== undefined && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      this._onCell(Number(t.dataset.addr));
    }
  }

  _onSubmit(e) {
    e.preventDefault();
    switch (e.target?.dataset?.form) {
      case "buscar": {
        const text = (this._form.q ?? "").trim();
        if (text && !this._pending.has("buscar")) this._search(text);
        return;
      }
      case "confirmar":
        return this._confirm();
      case "mover":
        return this._move();
      case "crear":
        return this._create();
      case "guardar":
        return this._save();
      default:
        return undefined;
    }
  }

  // Rendering

  _render() {
    if (!this._hass) return;
    const root = this.shadowRoot;
    const active = root.activeElement;
    if (this._wantFocus) {
      this._focus = this._wantFocus;
      this._wantFocus = null;
    } else if (active?.dataset?.f) {
      let start = null;
      let end = null;
      try {
        start = active.selectionStart;
        end = active.selectionEnd;
      } catch {
        // selects and some input types have no caret
      }
      this._focus = { f: active.dataset.f, start, end };
    } else {
      this._focus = null;
    }

    const body =
      { ficha: () => this._sheetHtml(), crear: () => this._createHtml(), editar: () => this._editHtml() }[this._screen]?.() ??
      this._searchHtml();
    root.innerHTML = `<style>${STYLE}</style>
      <ha-card header="${esc(this._config?.title ?? "Cajonera")}">
        <div class="wrap">
          <div data-live="warn">${this._warnHtml()}</div>
          ${body}
        </div>
      </ha-card>`;
    this._rendered = true;

    if (this._focus) {
      const el = root.querySelector(`[data-f="${this._focus.f}"]`);
      if (el) {
        el.focus();
        if (this._focus.start != null) {
          try {
            el.setSelectionRange(this._focus.start, this._focus.end);
          } catch {
            // not a text field
          }
        }
      }
    }
  }

  _renderLive() {
    const root = this.shadowRoot;
    const cell = root.activeElement?.dataset?.addr;
    const parts = { warn: () => this._warnHtml(), grid: () => this._gridHtml(), low: () => this._lowHtml() };
    root.querySelectorAll("[data-live]").forEach((el) => {
      const html = parts[el.dataset.live]?.();
      if (html !== undefined) el.innerHTML = html;
    });
    if (cell !== undefined) root.querySelector(`[data-addr="${Number(cell)}"]`)?.focus();
  }

  _renderSuggestions() {
    const box = this.shadowRoot.querySelector('[data-live="sugs"]');
    if (!box) return this._render();
    box.innerHTML = this._sugsHtml();
    this.shadowRoot.querySelector('[data-f="q"]')?.setAttribute("aria-expanded", String(this._sugs.length > 0));
  }
}

// The HTML of every screen lives in cajonera-views.js.
Object.assign(CajoneraCard.prototype, viewMethods);

if (!customElements.get("cajonera-card")) {
  customElements.define("cajonera-card", CajoneraCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "cajonera-card", name: "Cajonera", description: "Cajonera modular con InvenTree" });
}
