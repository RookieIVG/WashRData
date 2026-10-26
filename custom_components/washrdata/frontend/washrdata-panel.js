// WashRData panel: overview of all appliances, their cycles and how well
// programs are recognised. Plain web component, no build step. Texts come
// from the integration's translations (selector "panel").

// A static import: no top-level await, so the element is defined by the time
// Home Assistant's script load event fires and it creates the panel.
import { STYLE } from "./washrdata-panel-style.js";

const TEXT_PREFIX = "component.washrdata.selector.panel.options.";
const REFRESH_MS = 5000;
const NEW_PROGRAM = "__new__";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class WashRDataPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._texts = {};
    this._selected = null; // entry id
    this._open = null; // cycle id with details shown
    this._compare = {}; // cycle id -> program to compare with
    this._splitAt = {}; // cycle id -> split point tapped in the chart, seconds
    this._loaded = false;
    this._pending = null;
    this._last = 0;
    this._changed = new Set(); // entry ids with news since the last refresh
    // Properties set before the element was defined sit on the instance and
    // would hide the setters; hand them to the setters instead.
    for (const prop of ["narrow", "panel", "hass"]) {
      if (Object.prototype.hasOwnProperty.call(this, prop)) {
        const value = this[prop];
        delete this[prop];
        this[prop] = value;
      }
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._init();
    if (this._menu) {
      this._menu.hass = hass;
      this._menu.narrow = this.narrow;
    }
  }

  get hass() {
    return this._hass;
  }

  disconnectedCallback() {
    this._unsub?.then((unsub) => unsub()).catch(() => {});
    this._unsub = null;
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._subscribe();
  }

  async _init() {
    try {
      const res = await this._hass.callWS({
        type: "frontend/get_translations",
        language: this._hass.language,
        category: "selector",
        integration: ["washrdata"],
      });
      this._texts = res.resources || {};
    } catch (err) {
      this._texts = {};
    }
    await this._load();
    this._subscribe();
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage(
      (event) => {
        this._changed.add(event.entry_id);
        this._scheduleRefresh();
      },
      { type: "washrdata/subscribe" }
    );
  }

  // Power reports arrive every few seconds while a cycle runs; refresh at most every REFRESH_MS.
  _scheduleRefresh() {
    if (this._pending) return;
    const wait = Math.max(0, this._last + REFRESH_MS - Date.now());
    this._pending = setTimeout(() => {
      this._pending = null;
      this._refresh();
    }, wait);
  }

  // Only the running cycle is fetched; the full overview with all curves only
  // when stored cycles, programs or the evaluation changed (new revision).
  async _refresh() {
    this._last = Date.now();
    const ids = [...this._changed];
    this._changed.clear();
    try {
      for (const id of ids) {
        const device = this._devices.find((d) => d.entry_id === id);
        const live = await this._hass.callWS({ type: "washrdata/live", entry_id: id });
        if (!device || live.revision !== device.revision) return this._load();
        Object.assign(device, live);
      }
    } catch (err) {
      return this._load();
    }
    this._render();
  }

  async _load() {
    this._last = Date.now();
    try {
      const res = await this._hass.callWS({ type: "washrdata/overview" });
      this._devices = res.devices;
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._loaded = true;
    this._render();
  }

  t(key, vars = {}) {
    let text = this._texts[TEXT_PREFIX + key] ?? key;
    for (const [k, v] of Object.entries(vars)) text = text.replace(`{${k}}`, v);
    return text;
  }

  // -- formatting -------------------------------------------------------

  _when(ts) {
    return new Date(ts * 1000).toLocaleString(this._hass.locale?.language || this._hass.language, {
      weekday: "short",
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _num(value, digits) {
    return Number(value).toLocaleString(this._hass.language, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  }

  _duration(seconds) {
    const minutes = Math.round(seconds / 60);
    return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")} h`;
  }

  _percent(value) {
    return value == null ? "–" : `${Math.round(value * 100)} %`;
  }

  _money(value, currency) {
    if (value == null) return "–";
    try {
      return new Intl.NumberFormat(this._hass.language, { style: "currency", currency }).format(value);
    } catch (err) {
      return `${this._num(value, 2)} ${currency}`;
    }
  }

  // Signed minutes, e.g. "+3 min" when the prediction was too long.
  _offset(seconds) {
    if (seconds == null) return "–";
    const m = Math.round(seconds / 60);
    return `${m > 0 ? "+" : m < 0 ? "−" : "±"}${Math.abs(m)} min`;
  }

  // -- charts -----------------------------------------------------------

  _sparkline(curve) {
    if (!curve.length) return "";
    const peak = Math.max(...curve, 1);
    const w = 120;
    const h = 28;
    const step = w / Math.max(1, curve.length - 1);
    const pts = curve.map((v, i) => `${(i * step).toFixed(1)},${(h - (Math.sqrt(v / peak) * (h - 2)) - 1).toFixed(1)}`);
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
      <polyline points="${pts.join(" ")}" /></svg>`;
  }

  // Curves in watts per minute. The first is drawn on top in the accent colour.
  // Lines stretch with the card; labels are HTML so their size stays fixed.
  // `marks` are vertical lines at minutes, `plot` makes the chart tappable for that cycle id.
  _chart(series, { live = false, marks = [], plot = null } = {}) {
    const all = series.flatMap((s) => s.curve);
    if (!all.length) return `<div class="empty">${esc(this.t("no_curve"))}</div>`;
    const peak = Math.max(...all, 10);
    const minutes = Math.max(...series.map((s) => s.curve.length), 10);
    const x = (i) => (i / minutes) * 1000;
    const y = (v) => (1 - v / peak) * 1000;
    const path = (curve) => curve.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");

    let grid = "";
    let labels = "";
    for (const v of niceTicks(peak, 4)) {
      grid += `<line class="grid" x1="0" x2="1000" y1="${y(v)}" y2="${y(v)}" />`;
      const text = v >= 1000 ? `${this._num(v / 1000, 1)} kW` : `${v} W`;
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

  // -- rendering --------------------------------------------------------

  _device() {
    return this._devices.find((d) => d.entry_id === this._selected) || this._devices[0];
  }

  _render() {
    const root = this.shadowRoot;
    // Do not pull an open dropdown away from under the user; render when it closes.
    if (root.activeElement?.tagName === "SELECT") {
      if (!this._deferred) {
        this._deferred = true;
        root.activeElement.addEventListener("blur", () => this._render(), { once: true });
      }
      return;
    }
    this._deferred = false;
    const scroll = root.querySelector(".content")?.scrollTop ?? 0;
    root.innerHTML = `<style>${STYLE}</style>
      <div class="toolbar"><span class="menu"></span><h1>WashRData</h1></div>
      <div class="content">${this._body()}</div>`;
    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this.narrow;
    root.querySelector(".menu").appendChild(this._menu);
    root.querySelector(".content").scrollTop = scroll;
    this._bind();
  }

  _body() {
    if (!this._loaded) return `<div class="empty">…</div>`;
    if (this._error) return `<div class="card error">${esc(this._error)}</div>`;
    if (!this._devices.length) return `<div class="card empty">${esc(this.t("no_devices"))}</div>`;
    const device = this._device();
    this._selected = device.entry_id;
    const tabs =
      this._devices.length > 1
        ? `<div class="tabs">${this._devices
            .map(
              (d) =>
                `<button class="tab${d.entry_id === device.entry_id ? " active" : ""}" data-device="${esc(d.entry_id)}">${esc(d.title)}</button>`
            )
            .join("")}</div>`
        : "";
    return `${tabs}<div class="grid">
        ${this._now(device)}
        ${this._accuracy(device)}
      </div>
      ${this._stats(device)}
      ${this._evaluation(device)}
      ${this._cycles(device)}
      ${this._programs(device)}`;
  }

  _now(device) {
    const cycle = device.current;
    const match = device.match;
    let body;
    if (!cycle) {
      body = `<div class="state">${esc(this.t(`state_${device.state}`))}</div>`;
    } else {
      const program = cycle.program || match?.program;
      const rows = [
        [this.t("program"), program ? esc(program) + (match?.manual ? ` <span class="badge">${esc(this.t("manual"))}</span>` : "") : esc(this.t("unknown"))],
        [this.t("recognition"), match ? `${esc(match.recognised ?? this.t("unknown"))} · ${this._percent(match.recognition_confidence)}` : esc(this.t("too_early"))],
        [this.t("remaining"), match?.remaining_s != null ? this._duration(match.remaining_s) : "–"],
        [this.t("running_for"), `${this._duration(cycle.duration_s)} · ${this._num(cycle.energy_kwh, 2)} kWh`],
      ];
      if (cycle.cost != null) rows.push([this.t("cost_so_far"), this._money(cycle.cost, device.currency)]);
      const bar = match ? Math.round(match.recognition_confidence * 100) : 0;
      const refs = this._refs(device, program || match?.recognised, cycle.id);
      body = `<div class="state">${esc(this.t(`state_${device.state}`))}</div>
        <dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>
        <div class="meter" title="${bar} %"><div style="width:${bar}%"></div></div>
        ${this._chart([{ curve: cycle.curve, main: true }, ...refs], { live: true })}
        ${refs.length ? `<div class="legend"><span class="key main"></span>${esc(this.t("this_cycle"))}<span class="key ref"></span>${esc(this.t("references_of", { program: program || match?.recognised }))}</div>` : ""}`;
    }
    return `<section class="card"><h2>${esc(this.t("now"))}</h2>${body}</section>`;
  }

  _accuracy(device) {
    const acc = device.accuracy;
    const rate = acc.cycles ? acc.hits / acc.cycles : null;
    const rows = device.programs
      .map((p) => {
        const a = acc.programs[p] || { cycles: 0, hits: 0 };
        const refs = device.cycles.filter((c) => c.reference && c.program === p).length;
        return `<tr><td>${esc(p)}</td><td>${a.cycles}</td><td>${a.hits}</td><td>${a.cycles ? this._percent(a.hits / a.cycles) : "–"}</td><td>${refs}</td></tr>`;
      })
      .join("");
    return `<section class="card"><h2>${esc(this.t("accuracy"))}</h2>
      <div class="big">${rate == null ? "–" : `${Math.round(rate * 100)} %`}</div>
      <div class="muted">${esc(acc.cycles ? this.t("hits_of", { hits: acc.hits, cycles: acc.cycles }) : this.t("no_rated"))}</div>
      ${rows ? `<table><thead><tr><th>${esc(this.t("program"))}</th><th>${esc(this.t("rated"))}</th><th>${esc(this.t("hits"))}</th><th>${esc(this.t("rate"))}</th><th>${esc(this.t("references"))}</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
      <p class="muted small">${esc(this.t("accuracy_help"))}</p>
    </section>`;
  }

  // Per program: how long, how much energy and money, how often.
  _stats(device) {
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
        const span = cs.length > 1 ? `<span class="muted small"> (${this._duration(Math.min(...durations))}–${this._duration(Math.max(...durations))})</span>` : "";
        return `<tr><td>${esc(p)}</td><td>${cs.length}</td><td>${this._duration(avg(durations))}${span}</td>
          <td>${this._num(avg(cs.map((c) => c.energy_kwh)), 2)} kWh</td>
          ${withCost ? `<td>${costs.length ? this._money(avg(costs), device.currency) : "–"}</td>` : ""}
          <td>${this._num(cs.length / weeks, 1)}</td></tr>`;
      })
      .join("");
    return `<section class="card"><h2>${esc(this.t("statistics"))}</h2>
      <div class="scroll"><table><thead><tr><th>${esc(this.t("program"))}</th><th>${esc(this.t("runs"))}</th><th>${esc(this.t("avg_duration"))}</th>
        <th>${esc(this.t("avg_energy"))}</th>${withCost ? `<th>${esc(this.t("avg_cost"))}</th>` : ""}<th>${esc(this.t("per_week"))}</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="muted small">${esc(this.t("statistics_help"))}</p>
    </section>`;
  }

  // Leave-one-out replay of the stored cycles: thresholds and remaining time.
  _evaluation(device) {
    const ev = device.evaluation;
    const th = device.thresholds;
    let body;
    if (!ev) {
      body = `<div class="empty">${esc(this.t("evaluation_pending"))}</div>`;
    } else if (!ev.cycles) {
      body = `<div class="empty">${esc(this.t("evaluation_empty"))}</div>`;
    } else {
      const near = (a, b) => b != null && Math.abs(a - b) < 0.001;
      const rows = ev.thresholds
        .map((r) => {
          const marks = [near(r.threshold, th.show) ? this.t("now_show") : "", near(r.threshold, th.auto) ? this.t("now_auto") : ""]
            .filter(Boolean)
            .join(", ");
          const best = near(r.threshold, ev.recommended_show) || near(r.threshold, ev.recommended_auto);
          return `<tr class="${best ? "best" : ""}"><td>${this._percent(r.threshold)}${marks ? ` <span class="badge">${esc(marks)}</span>` : ""}</td>
            <td>${r.wrong_shown}</td><td>${r.never_shown}</td><td>${r.first_shown_min == null ? "–" : `${Math.round(r.first_shown_min)} min`}</td>
            <td>${r.auto_correct}</td><td>${r.auto_wrong}</td><td>${r.auto_missed}</td></tr>`;
        })
        .join("");
      const remaining = ev.remaining
        .map(
          (r) =>
            `<tr><td>${this._percent(r.progress)}</td><td>${r.cycles}</td><td>${r.mean_abs_error_s == null ? "–" : `${Math.round(r.mean_abs_error_s / 60)} min`}</td><td>${this._offset(r.bias_s)}</td></tr>`
        )
        .join("");
      let advice = `<p class="muted">${esc(this.t(ev.note === "no_safe_threshold" ? "recommend_none" : "recommend_need", { count: 5 }))}</p>`;
      if (ev.recommended_show != null && ev.recommended_auto != null) {
        const same = near(ev.recommended_show, th.show) && near(ev.recommended_auto, th.auto);
        advice = `<p>${esc(this.t("recommend", { show: this._percent(ev.recommended_show), auto: this._percent(ev.recommended_auto) }))}
          ${same ? `<span class="muted"> · ${esc(this.t("recommend_applied"))}</span>` : `<button class="text" data-thresholds="${ev.recommended_show},${ev.recommended_auto}">${esc(this.t("apply"))}</button>`}</p>`;
      }
      const newer = ev.new_program_cycles ? ` ${this.t("evaluation_new", { count: ev.new_program_cycles })}` : "";
      body = `<p class="muted">${esc(this.t("evaluation_intro", { count: ev.cycles }) + newer)}</p>
        ${advice}
        <h3>${esc(this.t("thresholds"))}</h3>
        <div class="scroll"><table><thead><tr><th>${esc(this.t("threshold"))}</th><th>${esc(this.t("wrong_shown"))}</th><th>${esc(this.t("never_shown"))}</th>
          <th>${esc(this.t("shown_after"))}</th><th>${esc(this.t("auto_correct"))}</th><th>${esc(this.t("auto_wrong"))}</th><th>${esc(this.t("auto_missed"))}</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <h3>${esc(this.t("remaining_accuracy"))}</h3>
        <table><thead><tr><th>${esc(this.t("at_progress"))}</th><th>${esc(this.t("runs"))}</th><th>${esc(this.t("mean_error"))}</th><th>${esc(this.t("bias"))}</th></tr></thead>
          <tbody>${remaining}</tbody></table>
        <p class="muted small">${esc(this.t("evaluation_help"))}</p>`;
    }
    return `<section class="card"><h2>${esc(this.t("evaluation"))}</h2>${body}</section>`;
  }

  _refs(device, program, exclude) {
    if (!program) return [];
    return device.cycles.filter((c) => c.reference && c.program === program && c.id !== exclude).map((c) => ({ curve: c.curve }));
  }

  _verdict(device, c) {
    if (c.confidence == null) return `<span class="muted">${esc(this.t("not_rated"))}</span>`;
    const sure = c.recognised && c.confidence >= device.thresholds.auto;
    const said = c.recognised ? `${esc(c.recognised)} · ${this._percent(c.confidence)}` : esc(this.t("unknown"));
    const rated = c.program && c.known_programs.includes(c.program) && !c.interrupted;
    const mark = !rated ? "" : sure && c.recognised === c.program ? `<span class="hit">✓</span>` : `<span class="miss">✗</span>`;
    const retro = c.recognition_retro ? ` <span class="badge">${esc(this.t("retro"))}</span>` : "";
    return `${mark}<span class="${sure ? "" : "muted"}">${said}</span>${retro}`;
  }

  _select(device, c) {
    const options = [
      `<option value=""${c.program ? "" : " selected"}>${esc(this.t("none"))}</option>`,
      ...device.programs.map((p) => `<option value="${esc(p)}"${p === c.program ? " selected" : ""}>${esc(p)}</option>`),
      `<option value="${NEW_PROGRAM}">${esc(this.t("new_program"))}</option>`,
    ];
    return `<select data-label="${esc(c.id)}" aria-label="${esc(this.t("program"))}">${options.join("")}</select>`;
  }

  _cycles(device) {
    if (!device.cycles.length) return `<section class="card"><h2>${esc(this.t("cycles"))}</h2><div class="empty">${esc(this.t("no_cycles"))}</div></section>`;
    const rows = device.cycles
      .map((c) => {
        const open = this._open === c.id;
        const source = c.program_source ? `<span class="badge">${esc(this.t(c.program_source))}</span>` : "";
        return `<div class="cycle${open ? " open" : ""}">
          <button class="row" data-open="${esc(c.id)}" aria-expanded="${open}">
            <span class="when">${esc(this._when(c.start))}</span>
            <span class="facts">${this._duration(c.duration_s)} · ${this._num(c.energy_kwh, 2)} kWh${c.cost != null ? ` · ${this._money(c.cost, device.currency)}` : ""}${c.interrupted ? ` · ${esc(this.t("interrupted"))}` : ""}${c.reference ? ` · ${esc(this.t("reference"))}` : ""}</span>
            ${this._sparkline(c.curve)}
          </button>
          <div class="labels">
            <label>${esc(this.t("program"))} ${source}${this._select(device, c)}</label>
            <div class="verdict"><span class="muted small">${esc(this.t("recognition"))}</span> ${this._verdict(device, c)}</div>
          </div>
          ${open ? this._detail(device, c) : ""}
        </div>`;
      })
      .join("");
    return `<section class="card"><h2>${esc(this.t("cycles"))}</h2><div class="cycles">${rows}</div></section>`;
  }

  _detail(device, c) {
    const fallback = c.program || c.recognised || device.programs[0] || "";
    const compare = this._compare[c.id] ?? fallback;
    const refs = this._refs(device, compare, c.id);
    const options = [`<option value="">${esc(this.t("nothing"))}</option>`, ...device.programs.map((p) => `<option value="${esc(p)}"${p === compare ? " selected" : ""}>${esc(p)}</option>`)];
    return `<div class="detail">
      <label class="compare">${esc(this.t("compare_with"))} <select data-compare="${esc(c.id)}">${options.join("")}</select></label>
      ${this._chart([{ curve: c.curve, main: true }, ...refs], { marks: this._splitMarks(c), plot: c.id })}
      <div class="legend"><span class="key main"></span>${esc(this.t("this_cycle"))}${compare ? `<span class="key ref"></span>${esc(refs.length ? this.t("references_of", { program: compare }) : this.t("no_references", { program: compare }))}` : ""}</div>
      <div class="muted small">${esc(this.t("peak"))} ${c.peak_w} W</div>
      ${this._replay(c)}
      ${this._split(c)}
    </div>`;
  }

  // Split points: the longest quiet stretches, or a point tapped in the chart.
  _splitMarks(c) {
    const marks = (c.split_points || []).map(([a, b]) => ({ minute: (a + b) / 120 }));
    const chosen = this._splitAt[c.id];
    if (chosen != null) marks.push({ minute: chosen / 60, chosen: true });
    return marks;
  }

  _split(c) {
    const buttons = (c.split_points || []).map(([a, b]) => {
      const at = Math.round((a + b) / 2);
      const label = this.t("split_at_pause", { time: this._duration(a), pause: Math.round((b - a) / 60) });
      return `<button class="text" data-split="${esc(c.id)}" data-at="${at}">${esc(label)}</button>`;
    });
    const chosen = this._splitAt[c.id];
    if (chosen != null) {
      buttons.push(
        `<button class="text" data-split="${esc(c.id)}" data-at="${Math.round(chosen)}">${esc(this.t("split_at", { time: this._duration(chosen) }))}</button>`
      );
    }
    return `<div class="split"><span class="muted small">${esc(this.t("split_help"))}</span>${buttons.join("")}</div>`;
  }

  _replay(c) {
    const e = c.evaluation;
    if (!e) return "";
    if (e.new_program) {
      const said = e.final ? this.t("replay_new_as", { program: e.final, confidence: this._percent(e.confidence) }) : this.t("replay_new_unknown");
      return `<div class="muted small">${esc(this.t("replay"))}: ${esc(said)}</div>`;
    }
    const parts = [
      e.first_shown_min != null ? this.t("replay_shown", { minute: e.first_shown_min }) : this.t("replay_never"),
      e.wrong_shown ? this.t("replay_wrong") : "",
      this.t("replay_remaining", { offset: this._offset(e.remaining_error_s["0.5"]) }),
    ].filter(Boolean);
    return `<div class="muted small">${esc(this.t("replay"))}: ${esc(parts.join(" · "))}</div>`;
  }

  _programs(device) {
    if (!device.programs.length) return "";
    const rows = device.programs
      .map((p) => {
        const count = device.cycles.filter((c) => c.program === p).length;
        return `<li><span>${esc(p)} <span class="muted">· ${esc(this.t("cycle_count", { count }))}</span></span>
          <span><button class="text" data-rename="${esc(p)}">${esc(this.t("rename"))}</button><button class="text danger" data-delete="${esc(p)}">${esc(this.t("delete"))}</button></span></li>`;
      })
      .join("");
    return `<section class="card"><h2>${esc(this.t("programs"))}</h2><ul class="programs">${rows}</ul></section>`;
  }

  // -- actions ----------------------------------------------------------

  _bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-device]").forEach((el) =>
      el.addEventListener("click", () => {
        this._selected = el.dataset.device;
        this._open = null;
        this._render();
      })
    );
    root.querySelectorAll("[data-open]").forEach((el) =>
      el.addEventListener("click", () => {
        this._open = this._open === el.dataset.open ? null : el.dataset.open;
        this._render();
      })
    );
    root.querySelectorAll("[data-compare]").forEach((el) =>
      el.addEventListener("change", () => {
        this._compare[el.dataset.compare] = el.value;
        this._render();
      })
    );
    root.querySelectorAll("[data-label]").forEach((el) =>
      el.addEventListener("change", () => this._label(el.dataset.label, el.value))
    );
    root.querySelectorAll("[data-rename]").forEach((el) =>
      el.addEventListener("click", () => this._rename(el.dataset.rename))
    );
    root.querySelectorAll("[data-delete]").forEach((el) =>
      el.addEventListener("click", () => this._delete(el.dataset.delete))
    );
    root.querySelectorAll("[data-plot]").forEach((el) =>
      el.addEventListener("click", (event) => {
        const box = el.getBoundingClientRect();
        const share = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
        this._splitAt[el.dataset.plot] = share * Number(el.dataset.minutes) * 60;
        this._render();
      })
    );
    root.querySelectorAll("[data-split]").forEach((el) =>
      el.addEventListener("click", () => this._splitCycle(el.dataset.split, Number(el.dataset.at)))
    );
    root.querySelectorAll("[data-thresholds]").forEach((el) =>
      el.addEventListener("click", () => {
        const [show, auto] = el.dataset.thresholds.split(",").map(Number);
        this._applyThresholds(show, auto);
      })
    );
  }

  async _call(msg) {
    try {
      await this._hass.callWS({ entry_id: this._selected, ...msg });
    } catch (err) {
      alert(err.message || String(err));
    }
    await this._load();
  }

  async _label(cycleId, value) {
    let program = value || null;
    if (value === NEW_PROGRAM) {
      program = (prompt(this.t("new_program_prompt")) || "").trim();
      if (!program) return this._render();
    }
    await this._call({ type: "washrdata/label", cycle_id: cycleId, program });
  }

  async _splitCycle(cycleId, at) {
    if (!confirm(this.t("split_confirm", { time: this._duration(at) }))) return;
    delete this._splitAt[cycleId];
    this._open = null;
    await this._call({ type: "washrdata/split_cycle", cycle_id: cycleId, at_s: at });
  }

  async _applyThresholds(show, auto) {
    if (!confirm(this.t("apply_confirm", { show: this._percent(show), auto: this._percent(auto) }))) return;
    await this._call({ type: "washrdata/set_thresholds", show, auto });
  }

  async _rename(program) {
    const name = (prompt(this.t("rename_prompt", { program }), program) || "").trim();
    if (name && name !== program) await this._call({ type: "washrdata/rename_program", program, new_name: name });
  }

  async _delete(program) {
    if (confirm(this.t("delete_confirm", { program }))) await this._call({ type: "washrdata/delete_program", program });
  }
}

function niceTicks(max, count) {
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || raw;
  const ticks = [];
  for (let v = 0; v <= max + 1e-9; v += step) ticks.push(Math.round(v));
  return ticks;
}


customElements.define("washrdata-panel", WashRDataPanel);
