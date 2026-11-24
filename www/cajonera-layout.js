// Pure layout helpers for cajonera-card. No DOM, no dependencies: tested with
// `node --test homeassistant/www/tests/`.
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
  const n = Number(q);
  if (Number.isInteger(n)) return String(n);
  return String(n).replace(".", ",");
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
