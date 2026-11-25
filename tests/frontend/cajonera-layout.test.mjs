import { test } from "node:test";
import assert from "node:assert/strict";

import {
  debounceMs,
  defaultDrawer,
  createSequencer,
  fmtQty,
  gridCells,
  litFromRows,
  minChars,
  panelModel,
  parseQty,
  rowLabel,
  suggestionRows,
} from "../../custom_components/cajonera/frontend/cajonera-layout.js";

const d = (direccion, nombre, x, y, extra = {}) => ({ direccion, nombre, x, y, ancho: 1, alto: 1, ...extra });

test("base is bottom-left A1", () => {
  const g = gridCells([d(0, "A1", 0, 0), d(9, "B1", 0, 1)]);
  assert.equal(g.rows, 2);
  assert.equal(g.cols, 1);
  assert.equal(g.cells.find((c) => c.nombre === "A1").row, 2);
  assert.equal(g.cells.find((c) => c.nombre === "B1").row, 1);
  assert.equal(g.cells.find((c) => c.nombre === "A1").col, 1);
});

test("double drawer spans two columns", () => {
  const g = gridCells([d(0, "A1", 0, 0), d(17, "B1", 0, 1, { ancho: 2 }), d(21, "B3", 2, 1)]);
  const b1 = g.cells.find((c) => c.direccion === 17);
  assert.equal(b1.colSpan, 2);
  assert.equal(b1.rowSpan, 1);
  assert.equal(g.cols, 3);
});

test("tall drawer spans two rows and sits on its bottom row", () => {
  const g = gridCells([d(0, "A1", 0, 0), d(5, "A2", 1, 0, { alto: 2 })]);
  const tall = g.cells.find((c) => c.direccion === 5);
  assert.equal(g.rows, 2);
  assert.equal(tall.row, 1);
  assert.equal(tall.rowSpan, 2);
});

test("unplaced and outside-grid drawers are listed apart", () => {
  const g = gridCells([d(0, "A1", 0, 0), d(8, null, null, null), d(9, null, -1, 0, { error: "fuera_reticula" })]);
  assert.deepEqual(g.cells.map((c) => c.direccion), [0]);
  assert.deepEqual(g.unplaced.map((c) => c.direccion), [8, 9]);
});

test("cells keep colour, low flag and error", () => {
  const g = gridCells([d(0, "A1", 0, 0, { rgb: [0, 0, 255], bajo: true, error: null, modo: "buscado" })]);
  const [c] = g.cells;
  assert.deepEqual(c.rgb, [0, 0, 255]);
  assert.equal(c.bajo, true);
  assert.equal(c.modo, "buscado");
});

test("empty map gives an empty grid", () => {
  assert.deepEqual(gridCells([]), { cols: 0, rows: 0, cells: [], unplaced: [] });
  assert.deepEqual(gridCells(undefined), { cols: 0, rows: 0, cells: [], unplaced: [] });
});

test("rowLabel spreadsheet style", () => {
  assert.equal(rowLabel(0), "A");
  assert.equal(rowLabel(25), "Z");
  assert.equal(rowLabel(26), "AA");
});

test("fmtQty", () => {
  assert.equal(fmtQty(40), "40");
  assert.equal(fmtQty("40.00000"), "40");
  assert.equal(fmtQty(2.5), "2,5");
});

test("fmtQty rounds float noise and never uses exponents", () => {
  assert.equal(fmtQty(0.3 - 0.1), "0,2");
  assert.equal(fmtQty(1000000.5), "1000000,5");
  assert.equal(fmtQty(1e21), "1000000000000000000000");
});

test("parseQty accepts comma or dot decimals", () => {
  assert.equal(parseQty("2,5"), 2.5);
  assert.equal(parseQty("2.5"), 2.5);
  assert.equal(parseQty("10"), 10);
  assert.equal(parseQty("  4  "), 4);
});

test("parseQty rejects empty, non-numeric and negative input", () => {
  assert.equal(parseQty(""), null);
  assert.equal(parseQty("   "), null);
  assert.equal(parseQty("abc"), null);
  assert.equal(parseQty("-1"), null);
});

test("parseQty rejects zero unless allowZero", () => {
  assert.equal(parseQty("0"), null);
  assert.equal(parseQty("0", { allowZero: true }), 0);
});

test("suggestionRows adds Crear only when there is no exact match and text has >= 2 chars", () => {
  const sugerencias = [{ nombre: "Tornillo M4" }, { nombre: "Tuerca" }];
  const partial = suggestionRows(sugerencias, "Tornillo");
  assert.equal(partial.length, 3);
  assert.deepEqual(partial.at(-1), { crear: true, nombre: "Tornillo" });

  const exact = suggestionRows(sugerencias, "tornillo m4");
  assert.equal(exact.length, 2);
  assert.equal(exact.some((r) => r.crear), false);

  const fresh = suggestionRows(sugerencias, "  Arandela ");
  assert.deepEqual(fresh.at(-1), { crear: true, nombre: "Arandela" });
  assert.equal(fresh.length, 3);
});

test("suggestionRows does not offer Crear for a single character", () => {
  const rows = suggestionRows([], "a");
  assert.deepEqual(rows, []);
  assert.deepEqual(suggestionRows([], "ab"), [{ crear: true, nombre: "ab" }]);
});

test("suggestionRows does not offer Crear when the exact match is past the 8-row cap", () => {
  const many = Array.from({ length: 12 }, (_, i) => ({ nombre: `item ${i}` }));
  const rows = suggestionRows(many, "ITEM 11");
  assert.equal(rows.length, 8);
  assert.equal(rows.some((r) => r.crear), false);
});

test("suggestionRows keeps at most 8 server rows", () => {
  const many = Array.from({ length: 12 }, (_, i) => ({ nombre: `item ${i}` }));
  const rows = suggestionRows(many, "item");
  assert.equal(rows.length, 9);
  assert.equal(rows.filter((r) => !r.crear).length, 8);
  assert.deepEqual(rows.at(-1), { crear: true, nombre: "item" });
});

test("acceptAnswer: only the latest sequence value is accepted", () => {
  const seq = createSequencer();
  const a = seq.next();
  const b = seq.next();
  assert.equal(seq.accept(a), false);
  assert.equal(seq.accept(b), true);
});

test("defaultDrawer picks the address with the most quantity", () => {
  assert.equal(
    defaultDrawer([
      { direccion: 3, cantidad: 2 },
      { direccion: 7, cantidad: 40 },
      { direccion: 9, cantidad: 5 },
    ]),
    7,
  );
});

test("defaultDrawer returns null when there are no drawers", () => {
  assert.equal(defaultDrawer([]), null);
  assert.equal(defaultDrawer(undefined), null);
});

test("debounce and minimum-length constants", () => {
  assert.equal(debounceMs, 300);
  assert.equal(minChars, 2);
});

test("parseQty rejects ambiguous thousands", () => {
  assert.equal(parseQty("1.000"), null);
  assert.equal(parseQty("1,000"), null);
  assert.equal(parseQty("12.500"), null);
  assert.equal(parseQty("1.000", { allowZero: true }), null);
});

test("parseQty rejects ambiguous thousands with leading zeros", () => {
  assert.equal(parseQty("01.000"), null);
  assert.equal(parseQty("007,000"), null);
  assert.equal(parseQty("0,125"), 0.125);
  assert.equal(parseQty("00.125"), 0.125);
});

test("parseQty accepts leading and trailing separators", () => {
  assert.equal(parseQty(",5"), 0.5);
  assert.equal(parseQty(".5"), 0.5);
  assert.equal(parseQty("2,"), 2);
  assert.equal(parseQty("2."), 2);
});

test("parseQty accepts three decimals below one", () => {
  assert.equal(parseQty("0,125"), 0.125);
  assert.equal(parseQty("0.125"), 0.125);
});

test("parseQty handles null and undefined", () => {
  assert.equal(parseQty(null), null);
  assert.equal(parseQty(undefined), null);
  assert.equal(parseQty(null, { allowZero: true }), null);
});

test("parseQty rejects malformed input", () => {
  assert.equal(parseQty("1e3"), null);
  assert.equal(parseQty("+2"), null);
  assert.equal(parseQty("1.5.2"), null);
  assert.equal(parseQty("2 5"), null);
  assert.equal(parseQty("."), null);
  assert.equal(parseQty(","), null);
});

const row = (nombre, ipn, direcciones, extra = {}) => ({ pieza_pk: 1, nombre, ipn, direcciones, bajo: false, ...extra });

test("litFromRows keeps the rows whose nombre contains the text", () => {
  const rows = [row("Huevo azul", "A-1", [18, 17]), row("Humo", "B-2", [21])];
  assert.deepEqual(litFromRows(rows, "hue"), { direcciones: [17, 18], bajos: [] });
  assert.deepEqual(litFromRows(rows, "hu"), { direcciones: [17, 18, 21], bajos: [] });
});

test("litFromRows matches the ipn too", () => {
  const rows = [row("Tornillo", "M3-10", [4]), row("Tuerca", "M4-20", [5])];
  assert.deepEqual(litFromRows(rows, "m4"), { direcciones: [5], bajos: [] });
});

test("litFromRows ignores case and surrounding blanks but not accents", () => {
  const rows = [row("Cañería", "X", [3]), row("Canasta", "Y", [8])];
  assert.deepEqual(litFromRows(rows, "  CAÑ "), { direcciones: [3], bajos: [] });
  assert.deepEqual(litFromRows(rows, "can"), { direcciones: [8], bajos: [] });
});

test("litFromRows skips rows without direcciones and tolerates null fields", () => {
  const rows = [row("Humo", null, undefined), row("Hule", "", []), { nombre: "Huso", direcciones: [9] }];
  assert.deepEqual(litFromRows(rows, "hu"), { direcciones: [9], bajos: [] });
  assert.deepEqual(litFromRows(undefined, "hu"), { direcciones: [], bajos: [] });
});

test("litFromRows ignores the create row", () => {
  const rows = [{ crear: true, nombre: "hu", direcciones: [1] }, row("Humo", "B", [2])];
  assert.deepEqual(litFromRows(rows, "hu"), { direcciones: [2], bajos: [] });
});

test("litFromRows de-duplicates and sorts; bajos are a subset of direcciones", () => {
  const rows = [row("Humo", "A", [21, 5], { bajo: true }), row("Hule", "B", [5, 30]), row("Huso", "C", [40], { bajo: false })];
  const { direcciones, bajos } = litFromRows(rows, "hu");
  assert.deepEqual(direcciones, [5, 21, 30, 40]);
  assert.deepEqual(bajos, [5, 21]);
  assert.ok(bajos.every((a) => direcciones.includes(a)));
});

test("panelModel: nothing to show", () => {
  assert.equal(panelModel(null), null);
  assert.equal(panelModel(undefined), null);
});

test("panelModel: search results show their phrase, the drawer column and the typed text", () => {
  const rows = [{ pieza_pk: 1, pieza: "Tornillo", cajon: "B3", cantidad: 4 }];
  const m = panelModel({ tipo: "resultados", frase: "Hay 4 tornillos.", resultados: rows, texto: "torn" });
  assert.equal(m.title, "Resultados de «torn»");
  assert.equal(m.phrase, "Hay 4 tornillos.");
  assert.deepEqual(m.rows, rows);
  assert.equal(m.showWhere, true);
});

test("panelModel: a search that found nothing has a phrase and no rows", () => {
  const m = panelModel({ tipo: "resultados", frase: "No encuentro nada.", resultados: [], texto: "zzz" });
  assert.equal(m.phrase, "No encuentro nada.");
  assert.deepEqual(m.rows, []);
});

test("panelModel: drawer contents carry the drawer name on every row and hide the drawer column", () => {
  const m = panelModel({ tipo: "cajon", cajon: "B3", direccion: 17, piezas: [{ pieza_pk: 1, pieza: "Tornillo", cantidad: 4 }] });
  assert.equal(m.title, "Cajón B3");
  assert.equal(m.showWhere, false);
  assert.deepEqual(m.rows, [{ pieza_pk: 1, pieza: "Tornillo", cantidad: 4, cajon: "B3" }]);
  assert.equal(m.phrase, "");
});

test("panelModel: an empty drawer says so, with the server's phrase when there is one", () => {
  assert.equal(panelModel({ tipo: "cajon", cajon: "B3", direccion: 17, piezas: [] }).phrase, "Vacío.");
  assert.equal(panelModel({ tipo: "cajon", cajon: "B3", direccion: 17, piezas: [], frase: "El cajón B3 está vacío." }).phrase, "El cajón B3 está vacío.");
});

test("panelModel: a drawer without a name is titled with a question mark", () => {
  assert.equal(panelModel({ tipo: "cajon", cajon: null, direccion: 17, piezas: [] }).title, "Cajón ?");
});
