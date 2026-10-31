// Styles of the WashRData panel, themed with Home Assistant's CSS variables.

export const STYLE = `
  :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif); }
  .toolbar { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 12px;
    background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff);
    border-bottom: var(--app-header-border-bottom, none); box-sizing: border-box; }
  .toolbar h1 { font-size: 20px; font-weight: 400; margin: 0; }
  .content { padding: 16px; max-width: 1100px; margin: 0 auto; box-sizing: border-box; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .card { background: var(--card-background-color, #fff); border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color); padding: 16px; margin-bottom: 16px; box-shadow: var(--ha-card-box-shadow, none); }
  .grid .card { margin-bottom: 0; }
  .grid { margin-bottom: 16px; }
  h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
  h3 { font-size: 14px; font-weight: 500; margin: 16px 0 0; }
  .scroll { overflow-x: auto; }
  tr.best td { background: var(--secondary-background-color, #f3f3f3); }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  button { font: inherit; color: inherit; }
  .tab { border: 1px solid var(--divider-color); background: var(--card-background-color); border-radius: 18px; padding: 6px 14px; cursor: pointer; }
  .tab.active { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .state { font-size: 22px; margin-bottom: 8px; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0 0 12px; }
  dt { color: var(--secondary-text-color); }
  dd { margin: 0; }
  .meter { height: 6px; border-radius: 3px; background: var(--divider-color); overflow: hidden; margin-bottom: 12px; }
  .meter div { height: 100%; background: var(--primary-color); }
  .big { font-size: 40px; font-weight: 300; }
  .muted { color: var(--secondary-text-color); }
  .small { font-size: 12px; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
  th, td { text-align: right; padding: 4px 6px; border-bottom: 1px solid var(--divider-color); }
  th:first-child, td:first-child { text-align: left; }
  th { font-weight: 500; color: var(--secondary-text-color); }
  .chart { padding: 8px 4px 22px 52px; }
  .plot { position: relative; height: 180px; }
  .detail .plot { height: 240px; }
  .plot svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .plot line, .plot path { vector-effect: non-scaling-stroke; }
  .plot .grid { stroke: var(--divider-color); stroke-width: 1; }
  .plot path { fill: none; stroke-linejoin: round; }
  .plot .main { stroke: var(--primary-color); stroke-width: 2; }
  .plot.tappable { cursor: crosshair; }
  .plot .mark { stroke: var(--secondary-text-color); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.7; }
  .plot .mark.chosen { stroke: var(--error-color, #c62828); stroke-dasharray: none; opacity: 1; }
  .split { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; margin-top: 8px; }
  .plot .ref { stroke: var(--secondary-text-color); stroke-width: 1.2; opacity: 0.45; }
  .ylabel, .xlabel { position: absolute; font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; }
  .ylabel { right: calc(100% + 6px); transform: translateY(-50%); }
  .xlabel { top: calc(100% + 4px); transform: translateX(-50%); }
  .legend { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 12px; color: var(--secondary-text-color); margin-top: 4px; }
  .key { display: inline-block; width: 16px; height: 3px; border-radius: 2px; }
  .key.main { background: var(--primary-color); }
  .key.ref { background: var(--secondary-text-color); opacity: 0.6; }
  .cycles { display: flex; flex-direction: column; }
  .cycle { border-bottom: 1px solid var(--divider-color); padding: 8px 0; }
  .cycle:last-child { border-bottom: none; }
  .row { display: grid; grid-template-columns: minmax(110px, max-content) 1fr 120px; gap: 12px; align-items: center;
    width: 100%; background: none; border: none; text-align: left; padding: 4px 0; cursor: pointer; }
  .when { font-weight: 500; }
  .spark { width: 120px; height: 28px; }
  .spark polyline { fill: none; stroke: var(--primary-color); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .labels { display: flex; flex-wrap: wrap; gap: 8px 24px; align-items: center; margin-top: 4px; }
  .labels label { display: flex; align-items: center; gap: 8px; }
  select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 6px; max-width: 220px; }
  .verdict { display: flex; gap: 6px; align-items: center; }
  .badge { font-size: 11px; padding: 1px 6px; border-radius: 8px; background: var(--secondary-background-color, #eee); color: var(--secondary-text-color); }
  .hit { color: var(--success-color, #2e7d32); font-weight: 700; }
  .unsure { color: var(--secondary-text-color); font-weight: 700; }
  .miss { color: var(--error-color, #c62828); font-weight: 700; }
  .detail { margin-top: 12px; }
  .compare { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .programs { list-style: none; margin: 0; padding: 0; }
  .programs li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
  .programs li:last-child { border-bottom: none; }
  .programs li > span:last-child { white-space: nowrap; }
  .text { background: none; border: none; color: var(--primary-color); cursor: pointer; padding: 4px 8px; }
  .text.danger { color: var(--error-color, #c62828); }
  .empty { color: var(--secondary-text-color); padding: 8px 0; }
  .error { color: var(--error-color, #c62828); }
  @media (max-width: 600px) {
    .content { padding: 8px; }
    .grid { grid-template-columns: 1fr; }
    .row { grid-template-columns: 1fr 90px; }
    .row .facts { grid-column: 1; grid-row: 2; font-size: 13px; color: var(--secondary-text-color); }
    .row .spark { grid-column: 2; grid-row: 1 / span 2; width: 90px; }
  }
`;
