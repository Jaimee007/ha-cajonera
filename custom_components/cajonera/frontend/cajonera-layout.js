// Pure layout helpers for cajonera-card. No DOM, no dependencies: tested with
// `node --test tests/frontend/cajonera-layout.test.mjs`.
//
// Grid convention (docs/09_gestion_inventario.md section 3.1): the base is the
// bottom-left drawer at (0, 0) = "A1"; rows grow upwards, columns rightwards.
// CSS grid rows grow downwards, so rows are flipped here.

const EMPTY = () => ({ cols: 0, rows: 0, cells: [], unplaced: [] });

export function rowLabel(y) {
  // Spreadsheet-style bijective base 26: 0 -> A, 25 -> Z, 26 -> AA.
  let n = y + 1;
  let out = "";
  while (n > 0) {
    const rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

export function fmtQty(q) {
  // Same rule as the Python side: up to 3 decimals, decimal comma, no exponents.
  const n = Number(q);
  if (!Number.isFinite(n)) return "0";
  return n.toLocaleString("es-ES", { maximumFractionDigits: 3, useGrouping: false });
}

const placed = (c) => Number.isInteger(c.x) && Number.isInteger(c.y) && c.x >= 0 && c.y >= 0;

export function gridCells(cajones) {
  if (!Array.isArray(cajones) || cajones.length === 0) return EMPTY();
  const inGrid = cajones.filter(placed);
  const unplaced = cajones.filter((c) => !placed(c));
  const span = (c, key) => Math.max(1, Number(c[key]) || 1);
  const cols = inGrid.reduce((m, c) => Math.max(m, c.x + span(c, "ancho")), 0);
  const rows = inGrid.reduce((m, c) => Math.max(m, c.y + span(c, "alto")), 0);
  const cells = inGrid.map((c) => ({
    direccion: c.direccion,
    nombre: c.nombre,
    col: c.x + 1,
    row: rows - c.y - (span(c, "alto") - 1),
    colSpan: span(c, "ancho"),
    rowSpan: span(c, "alto"),
    rgb: c.rgb || [0, 0, 0],
    modo: c.modo || "reposo",
    bajo: Boolean(c.bajo),
    error: c.error || null,
    abierto: Boolean(c.abierto),
  }));
  return { cols, rows, cells, unplaced };
}

export const debounceMs = 300;
export const minChars = 2;

const QTY_PATTERN = /^\d+(?:[.,]\d+)?$/;

export function parseQty(text, { allowZero = false } = {}) {
  // Accepts "2,5" and "2.5"; anything else (empty, letters, sign) is rejected.
  const t = String(text ?? "").trim();
  if (!QTY_PATTERN.test(t)) return null;
  const n = Number(t.replace(",", "."));
  if (!Number.isFinite(n)) return null;
  if (n === 0) return allowZero ? 0 : null;
  return n;
}

const MAX_SERVER_ROWS = 8;

export function suggestionRows(sugerencias, texto) {
  // Server rows first (capped), then a "create" row when the text is new.
  const all = Array.isArray(sugerencias) ? sugerencias : [];
  const rows = all.slice(0, MAX_SERVER_ROWS);
  const nombre = String(texto ?? "").trim();
  if (nombre.length < minChars) return rows;
  // Check the full server list, not the capped one: an exact match past the cap still counts.
  const lower = nombre.toLowerCase();
  const exists = all.some((r) => String(r.nombre ?? "").trim().toLowerCase() === lower);
  return exists ? rows : [...rows, { crear: true, nombre }];
}

export function createSequencer() {
  // Lets an async caller drop answers that were superseded by a newer request.
  let latest = 0;
  return {
    next() {
      latest += 1;
      return latest;
    },
    accept(n) {
      return n === latest;
    },
  };
}

export function defaultDrawer(cajones) {
  // Address holding the largest quantity; the first one wins on ties.
  if (!Array.isArray(cajones) || cajones.length === 0) return null;
  let best = cajones[0];
  for (const c of cajones) {
    if (Number(c.cantidad) > Number(best.cantidad)) best = c;
  }
  return best.direccion;
}
