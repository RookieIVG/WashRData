// The statistics and evaluation cards of the WashRData panel. `panel` is the panel,
// for its texts and number formats.

import { esc } from "./washrdata-panel-chart.js";

// Per program: how long, how much energy and money, how often.
export function statsCard(panel, device) {
  const done = device.cycles.filter((c) => c.program && !c.interrupted);
  if (!done.length) return "";
  const starts = device.cycles.map((c) => c.start);
  const weeks = Math.max(1, (Math.max(...starts) - Math.min(...starts)) / 604800);
  const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const withCost = done.some((c) => c.cost != null);
  const rows = device.programs
    .map((p) => {
      const cs = done.filter((c) => c.program === p);
      if (!cs.length) return "";
      const durations = cs.map((c) => c.duration_s);
      const costs = cs.map((c) => c.cost).filter((v) => v != null);
      const span = cs.length > 1 ? `<span class="muted small"> (${panel._duration(Math.min(...durations))}–${panel._duration(Math.max(...durations))})</span>` : "";
      return `<tr><td>${esc(p)}</td><td>${cs.length}</td><td>${panel._duration(avg(durations))}${span}</td>
        <td>${panel._num(avg(cs.map((c) => c.energy_kwh)), 2)} kWh</td>
        ${withCost ? `<td>${costs.length ? panel._money(avg(costs), device.currency) : "–"}</td>` : ""}
        <td>${panel._num(cs.length / weeks, 1)}</td></tr>`;
    })
    .join("");
  return `<section class="card"><h2>${esc(panel.t("statistics"))}</h2>
    <div class="scroll"><table><thead><tr><th>${esc(panel.t("program"))}</th><th>${esc(panel.t("runs"))}</th><th>${esc(panel.t("avg_duration"))}</th>
      <th>${esc(panel.t("avg_energy"))}</th>${withCost ? `<th>${esc(panel.t("avg_cost"))}</th>` : ""}<th>${esc(panel.t("per_week"))}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <p class="muted small">${esc(panel.t("statistics_help"))}</p>
  </section>`;
}

// Leave-one-out replay of the stored cycles: thresholds and remaining time.
export function evaluationCard(panel, device) {
  const ev = device.evaluation;
  const th = device.thresholds;
  let body;
  if (!ev) {
    body = `<div class="empty">${esc(panel.t("evaluation_pending"))}</div>`;
  } else if (!ev.cycles) {
    body = `<div class="empty">${esc(panel.t("evaluation_empty"))}</div>`;
  } else {
    const near = (a, b) => b != null && Math.abs(a - b) < 0.001;
    const rows = ev.thresholds
      .map((r) => {
        const marks = [near(r.threshold, th.show) ? panel.t("now_show") : "", near(r.threshold, th.auto) ? panel.t("now_auto") : ""]
          .filter(Boolean)
          .join(", ");
        const best = near(r.threshold, ev.recommended_show) || near(r.threshold, ev.recommended_auto);
        return `<tr class="${best ? "best" : ""}"><td>${panel._percent(r.threshold)}${marks ? ` <span class="badge">${esc(marks)}</span>` : ""}</td>
          <td>${r.wrong_shown}</td><td>${r.never_shown}</td><td>${r.first_shown_min == null ? "–" : `${Math.round(r.first_shown_min)} min`}</td>
          <td>${r.auto_correct}</td><td>${r.auto_wrong}</td><td>${r.auto_missed}</td></tr>`;
      })
      .join("");
    const remaining = ev.remaining
      .map(
        (r) =>
          `<tr><td>${r.progress == null ? esc(panel.t("from_cooling")) : panel._percent(r.progress)}</td><td>${r.cycles}</td><td>${r.mean_abs_error_s == null ? "–" : `${Math.round(r.mean_abs_error_s / 60)} min`}</td><td>${panel._offset(r.bias_s)}</td></tr>`
      )
      .join("");
    let advice = `<p class="muted">${esc(panel.t(ev.note === "no_safe_threshold" ? "recommend_none" : "recommend_need", { count: 5 }))}</p>`;
    if (ev.recommended_show != null && ev.recommended_auto != null) {
      const same = near(ev.recommended_show, th.show) && near(ev.recommended_auto, th.auto);
      advice = `<p>${esc(panel.t("recommend", { show: panel._percent(ev.recommended_show), auto: panel._percent(ev.recommended_auto) }))}
        ${same ? `<span class="muted"> · ${esc(panel.t("recommend_applied"))}</span>` : `<button class="text" data-thresholds="${ev.recommended_show},${ev.recommended_auto}">${esc(panel.t("apply"))}</button>`}</p>`;
    }
    const newer = ev.new_program_cycles ? ` ${panel.t("evaluation_new", { count: ev.new_program_cycles })}` : "";
    body = `<p class="muted">${esc(panel.t("evaluation_intro", { count: ev.cycles }) + newer)}</p>
      ${advice}
      <h3>${esc(panel.t("thresholds"))}</h3>
      <div class="scroll"><table><thead><tr><th>${esc(panel.t("threshold"))}</th><th>${esc(panel.t("wrong_shown"))}</th><th>${esc(panel.t("never_shown"))}</th>
        <th>${esc(panel.t("shown_after"))}</th><th>${esc(panel.t("auto_correct"))}</th><th>${esc(panel.t("auto_wrong"))}</th><th>${esc(panel.t("auto_missed"))}</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <h3>${esc(panel.t("remaining_accuracy"))}</h3>
      <table><thead><tr><th>${esc(panel.t("at_progress"))}</th><th>${esc(panel.t("runs"))}</th><th>${esc(panel.t("mean_error"))}</th><th>${esc(panel.t("bias"))}</th></tr></thead>
        <tbody>${remaining}</tbody></table>
      <p class="muted small">${esc(panel.t("evaluation_help"))}</p>`;
  }
  return `<section class="card"><h2>${esc(panel.t("evaluation"))}</h2>${body}</section>`;
}
