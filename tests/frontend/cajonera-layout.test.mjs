import { test } from "node:test";
import assert from "node:assert/strict";

import { fmtQty, gridCells, rowLabel } from "../../custom_components/cajonera/frontend/cajonera-layout.js";

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
