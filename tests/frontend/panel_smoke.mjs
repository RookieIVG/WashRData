// Browser test of the WashRData panel with data from a real Home Assistant
// (written by tests/integration/test_panel_data.py). Run after pytest:
//   npm install --no-save playwright && node tests/frontend/panel_smoke.mjs
import assert from "node:assert/strict";
import { readFile, mkdir } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, devices, webkit } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const files = {
  "/washrdata-panel.js": "custom_components/washrdata/frontend/washrdata-panel.js",
  "/washrdata-panel-style.js": "custom_components/washrdata/frontend/washrdata-panel-style.js",
  "/washrdata-panel-chart.js": "custom_components/washrdata/frontend/washrdata-panel-chart.js",
  "/overview.json": "tests/frontend/overview.json",
  "/de.json": "custom_components/washrdata/translations/de.json",
};
const page = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>html{--primary-color:#03a9f4;--primary-background-color:#fafafa;--card-background-color:#fff;
--primary-text-color:#212121;--secondary-text-color:#727272;--divider-color:#e0e0e0}body{margin:0}</style></head><body>
<script>customElements.define("ha-menu-button", class extends HTMLElement {});</script>
<script type="module">
  const overview = await (await fetch("/overview.json")).json();
  const de = await (await fetch("/de.json")).json();
  const resources = Object.fromEntries(Object.entries(de.selector.panel.options)
    .map(([k, v]) => ["component.washrdata.selector.panel.options." + k, v]));
  window.calls = [];
  window.emit = null;
  window.revision = overview.devices[0].revision;
  const hass = {
    language: "de",
    locale: { language: "de" },
    callWS: async (msg) => {
      window.calls.push(msg);
      if (msg.type === "frontend/get_translations") return { resources };
      if (msg.type === "washrdata/overview") return overview;
      if (msg.type === "washrdata/live") {
        const d = overview.devices[0];
        return { entry_id: d.entry_id, state: d.state, current: d.current, match: d.match, revision: window.revision };
      }
      return null;
    },
    connection: { subscribeMessage: async (cb) => { window.emit = cb; return () => {}; } },
  };
  const load = () => new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.type = "module";
    script.src = "/washrdata-panel.js?v=test";
    script.onload = resolve;
    script.onerror = reject;
    document.head.appendChild(script);
  });
  // Like Home Assistant: a module script tag, the element is created on its
  // load event. "?early" creates it before the module is there, as on a slow
  // phone, so the element is upgraded with hass already set.
  const el = document.createElement("washrdata-panel");
  if (location.search.includes("early")) {
    el.hass = hass;
    document.body.appendChild(el);
    await load();
  } else {
    await load();
    el.hass = hass;
    document.body.appendChild(el);
  }
</script></body></html>`;

const server = http.createServer(async (req, res) => {
  const url = req.url.split("?")[0];
  if (url === "/") return res.end(page);
  if (!files[url]) return res.writeHead(404).end();
  const type = url.endsWith(".js") ? "text/javascript" : "application/json";
  res.writeHead(200, { "content-type": type }).end(await readFile(path.join(root, files[url])));
});
await new Promise((r) => server.listen(0, r));
const base = `http://localhost:${server.address().port}/`;
const overview = JSON.parse(await readFile(path.join(root, files["/overview.json"])));
const device = overview.devices[0];

// WebKit is the engine of every browser on the iPhone, including the Home Assistant app.
const engines = { chromium, webkit };
const names = (process.env.BROWSERS || "chromium").split(",");
const shots = path.join(root, "tests/frontend/screenshots");
await mkdir(shots, { recursive: true });
let failed = false;
let browser;
for (const engine of names) {
  browser = await engines[engine].launch();
  try {
    await check(engine);
  } catch (err) {
    failed = true;
    console.error(`${engine}:`, err);
  } finally {
    await browser.close();
  }
}
server.close();
process.exit(failed ? 1 : 0);

async function check(engine) {
  const phone = engine === "webkit" ? devices["iPhone 13"] : { viewport: { width: 390, height: 900 } };
  for (const [name, options, query] of [
    ["desktop", { viewport: { width: 1200, height: 900 } }, ""],
    ["phone", phone, "?early"],
  ]) {
    const p = await browser.newPage(options);
    const errors = [];
    p.on("pageerror", (e) => errors.push(e.message));
    p.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Feinwäsche" : undefined));
    await p.goto(base + query);
    const panel = p.locator("washrdata-panel");
    await panel.locator(".cycle").first().waitFor();

    // Every section is there, in German.
    const headings = await panel.locator("h2").allInnerTexts();
    for (const h of ["Jetzt", "Erkennungsgenauigkeit", "Statistik je Programm", "Auswertung", "Zyklen", "Programme"]) {
      assert.ok(headings.includes(h), `${name}: section ${h} in ${headings}`);
    }
    assert.equal(await panel.locator(".cycle").count(), device.cycles.length);
    const text = await panel.locator(".content").innerText();
    assert.match(text, /5 von 6 bewerteten Läufen richtig erkannt/);
    // ✓ sure and right, ○ right but too unsure, ✗ a new program that would get another name.
    assert.match(text, /1 weitere richtig getippt, aber zu unsicher/);
    assert.match(text, /○\s*Kurz 30 · 30 %\s*richtig, aber zu unsicher zum Benennen/);
    assert.match(text, /✗\s*Kurz 30 · 80 %\s*neues Programm, hätte diesen Namen bekommen/);
    assert.ok((await panel.locator(".verdict .hit").count()) >= 4);
    assert.match(text, /nachträglich/);
    assert.match(text, /Kosten bisher/);
    assert.match(text, /€/);
    assert.ok(!/\b[a-z]+_[a-z_]+\b/.test(text.replace(/sensor\.\S+/g, "")), `${name}: untranslated key in panel`);

    // A cycle opens with its large chart over the references and the replay.
    await panel.locator(`button.row[data-open="${device.cycles.find((c) => c.evaluation).id}"]`).click();
    const detail = panel.locator(".detail");
    await detail.waitFor();
    assert.ok((await detail.locator("path.ref").count()) >= 1, "references drawn");
    assert.match(await detail.innerText(), /Nachgespielt: richtiges Programm ab Minute \d+/);
    await p.screenshot({ path: path.join(shots, `${engine}-${name}.png`), fullPage: true });

    // Live updates fetch only the running cycle while nothing else changed ...
    await p.evaluate(() => (window.calls = []));
    await p.evaluate((id) => window.emit({ entry_id: id }), device.entry_id);
    await p.waitForFunction(() => window.calls.length > 0);
    await p.waitForTimeout(300);
    const types = await p.evaluate(() => window.calls.map((c) => c.type));
    assert.deepEqual(types, ["washrdata/live"]);
    // ... and everything once the stored cycles changed.
    await p.evaluate(() => { window.calls = []; window.revision = "changed"; });
    await p.waitForTimeout(5200);
    await p.evaluate((id) => window.emit({ entry_id: id }), device.entry_id);
    await p.waitForFunction(() => window.calls.some((c) => c.type === "washrdata/overview"), null, { timeout: 8000 });

    assert.deepEqual(errors, [], `${name}: page errors`);
    await p.close();
  }
  // Labelling sends the right commands, a new program asks for its name, and
  // the recommended thresholds can be applied.
  const p = await browser.newPage();
  p.on("dialog", (d) => d.accept(d.type() === "prompt" ? "Feinwäsche" : undefined));
  await p.goto(base);
  const panel = p.locator("washrdata-panel");
  await panel.locator(".cycle").first().waitFor();
  const cycle = device.cycles[1];
  const other = device.programs.find((x) => x !== cycle.program);
  await panel.locator(`select[data-label="${cycle.id}"]`).selectOption(other);
  await panel.locator(`select[data-label="${device.cycles[2].id}"]`).selectOption("__new__");
  await panel.locator("[data-thresholds]").click();
  // The merged cycle offers its gap as split point; a tap in the chart adds another.
  const merged = device.cycles.find((c) => c.split_points.length && c.duration_s > 5 * 3600);
  await panel.locator(`button.row[data-open="${merged.id}"]`).click();
  const [gapStart, gapEnd] = merged.split_points[0];
  await panel.locator(".cycle.open").screenshot({ path: path.join(shots, `${engine}-split.png`) });
  await panel.locator(`[data-split="${merged.id}"]`).first().click();
  await panel.locator(`button.row[data-open="${merged.id}"]`).click();
  const plot = panel.locator(`[data-plot="${merged.id}"]`);
  const box = await plot.boundingBox();
  await plot.click({ position: { x: box.width / 4, y: box.height / 2 } });
  await panel.locator(`[data-split="${merged.id}"]`).last().click();
  await p.waitForTimeout(300);
  const sent = (await p.evaluate(() => window.calls)).filter((c) => !["frontend/get_translations", "washrdata/overview"].includes(c.type));
  assert.deepEqual(sent, [
    { entry_id: device.entry_id, type: "washrdata/label", cycle_id: cycle.id, program: other },
    { entry_id: device.entry_id, type: "washrdata/label", cycle_id: device.cycles[2].id, program: "Feinwäsche" },
    {
      entry_id: device.entry_id,
      type: "washrdata/set_thresholds",
      show: device.evaluation.recommended_show,
      auto: device.evaluation.recommended_auto,
    },
    { entry_id: device.entry_id, type: "washrdata/split_cycle", cycle_id: merged.id, at_s: Math.round((gapStart + gapEnd) / 2) },
    sent[4],
  ]);
  // The tapped point lies a quarter into the chart.
  assert.equal(sent[4].type, "washrdata/split_cycle");
  assert.ok(Math.abs(sent[4].at_s - merged.duration_s / 4) < 300, `tapped split at ${sent[4].at_s}`);
  console.log(`${engine}: panel smoke test passed`);
}
