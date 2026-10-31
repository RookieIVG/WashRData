// Charts of the WashRData panel as SVG strings, and the HTML escaping they share.

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function niceTicks(max, count) {
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(Math.round(v));
  return ticks;
}

// A small curve for the cycle list, square root scale so the motor shows next to the heater.
export function sparkline(curve) {
  if (!curve.length) return "";
  const peak = Math.max(...curve, 1);
  const w = 120;
  const h = 28;
  const step = w / Math.max(1, curve.length - 1);
  const pts = curve.map((v, i) => `${(i * step).toFixed(1)},${(h - Math.sqrt(v / peak) * (h - 2) - 1).toFixed(1)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts.join(" ")}" /></svg>`;
}

// Curves in watts per minute. The first is drawn on top in the accent colour.
// Lines stretch with the card; labels are HTML so their size stays fixed.
// `marks` are vertical lines at minutes, `plot` makes the chart tappable for that cycle id.
// `num` formats numbers in the user's language, `empty` is shown without any curve.
export function chart(series, { live = false, marks = [], plot = null } = {}, { num, empty }) {
  const all = series.flatMap((s) => s.curve);
  if (!all.length) return `<div class="empty">${esc(empty)}</div>`;
  const peak = Math.max(...all, 10);
  const minutes = Math.max(...series.map((s) => s.curve.length), 10);
  const x = (i) => (i / minutes) * 1000;
  const y = (v) => (1 - v / peak) * 1000;
  const path = (curve) => curve.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");

  let grid = "";
  let labels = "";
  for (const v of niceTicks(peak, 4)) {
    grid += `<line class="grid" x1="0" x2="1000" y1="${y(v)}" y2="${y(v)}" />`;
    const text = v >= 1000 ? `${num(v / 1000, 1)} kW` : `${v} W`;
    labels += `<span class="ylabel" style="top:${y(v) / 10}%">${text}</span>`;
  }
  const xStep = minutes > 150 ? 60 : 30;
  for (let m = 0; m <= minutes; m += xStep) {
    labels += `<span class="xlabel" style="left:${x(m) / 10}%">${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}</span>`;
  }
  const lines = [...series]
    .reverse()
    .map((s) => `<path class="${s.main ? "main" : "ref"}${live && s.main ? " live" : ""}" d="${path(s.curve)}" />`)
    .join("");
  for (const mark of marks) {
    grid += `<line class="mark${mark.chosen ? " chosen" : ""}" x1="${x(mark.minute)}" x2="${x(mark.minute)}" y1="0" y2="1000" />`;
  }
  const tap = plot ? ` data-plot="${esc(plot)}" data-minutes="${minutes}"` : "";
  return `<div class="chart"><div class="plot${plot ? " tappable" : ""}"${tap}>
    <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" role="img">${grid}${lines}</svg>${labels}
  </div></div>`;
}
