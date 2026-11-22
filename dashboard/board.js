// Breadboard geometry and drawing, shared by the dashboard (the layout to copy) and the
// simulator page (the board you build). Drawn like the real board on the bench: standing up,
// rows 1-60 from top to bottom, the a-e half with its + and - rails on the left, the f-j half with
// its own rails on the right. The ESP32 sits beside the board, connected with jumper wires.
//
// Hole names match laptop/benchcheck/layout.py: "4c" on the board is "c:4"; "+:4" and "-:4" are the
// left + and - rails at row 4. The f-j half and the right rails exist (the sim can use them) but
// the laptop's layout only uses the a-e half and the left rails.
const BB = (() => {
  const NROWS = 60, P = 18, ROWS = "abcde", ROWS2 = "fghij";
  const NODE_COLORS = ["#2f6fde", "#c2410c", "#7c3aed", "#0f766e", "#b45309", "#be185d", "#4d7c0f"];
  const NS = "http://www.w3.org/2000/svg";

  // x positions, left to right (node labels sit left of the board, the ESP32 on its right half)
  const BX = 86;
  const X = {};
  X["+"] = BX + 18; X["-"] = X["+"] + P;
  X.a = X["-"] + 2.2 * P; [...ROWS].forEach((r, i) => X[r] = X.a + i * P);
  X.f = X.e + 2.4 * P; [...ROWS2].forEach((r, i) => X[r] = X.f + i * P);
  X["R+"] = X.j + 2.2 * P; X["R-"] = X["R+"] + P;
  const BOARD_RIGHT = X["R-"] + 18;
  // The ESP32-S3-DevKitC-1: left pin row in column j from row 1 (3V3) to row 22 (GND); the board is
  // about ten holes wide, so it covers the right rails and its other pin row hangs off the edge.
  const ESP_LEFT = X.j - 0.6 * P, ESP_W = 10.2 * P, W = ESP_LEFT + ESP_W + 8;
  const Y0 = 48, rowY = r => Y0 + (Number(r) - 1) * P, H = rowY(NROWS) + 40;
  const ESP_HEADER = ["3V3", "3V3", "RST", "4", "5", "6", "7", "15", "16", "17", "18", "8", "3", "46",
                      "9", "10", "11", "12", "13", "14", "5V", "GND"];

  let espTop = 1;  // row of the ESP32's 3V3 pin in the layout being drawn
  const holeXY = id => {
    if (id === "esp:3v3") return [X.f, rowY(espTop)];                           // the 3V3 pin's row, hole f
    if (id === "esp:gnd") return [X.f, rowY(espTop + ESP_HEADER.length - 1)];   // the GND pin's row, hole f
    const [k, r] = id.split(":");
    return [X[k], rowY(r)];
  };
  const netOf = id => {
    if (id.startsWith("esp:")) return id;
    const [k, r] = id.split(":");
    if (k === "+" || k === "-") return "rail" + k;
    if (k === "R+" || k === "R-") return "railR" + k[1];
    return (ROWS.includes(k) ? "col" : "colR") + r;
  };
  const holeName = id => {
    if (id === "esp:3v3") return "ESP32 3V3"; if (id === "esp:gnd") return "ESP32 GND";
    const [k, r] = id.split(":");
    if (k === "+" || k === "-") return `${k === "+" ? "+" : "−"} rail (row ${r})`;
    if (k === "R+" || k === "R-") return `right ${k === "R+" ? "+" : "−"} rail (row ${r})`;
    return `${r}${k}`;
  };
  const fmtOhms = v => v >= 1e6 ? v / 1e6 + "M" : v >= 1e3 ? v / 1e3 + "k" : String(v);

  function el(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    if (parent) parent.append(e);
    return e;
  }

  // opts: layout {node_columns, probes, socket, extra_columns}; parts [{id, kind: R|W|P3|PG, value|label, a, b,
  //   loose, state, name}] with state "done" | "verified" | "pending" | "current" | "future" | "selected";
  // highlight: holes of the part to place now (their whole rows light up); socketActive; probesCurrent;
  // problemNodes: nodes a failed check blames (their rows get a soft red tint); a part with `suspect: true`
  // gets a dashed red pulsing outline;
  // onHole / onPart make it clickable; firstHole marks a half-placed part.
  // (layout.node_columns maps a node to its row number: the "column" of the strip it owns.)
  // Let the user drag `g` up or down by whole rows; on release, call done(newRow) if it moved.
  function dragRows(svg, g, startRow, done, min, max) {
    g.style.cursor = "ns-resize";
    g.addEventListener("pointerdown", e => {
      e.preventDefault(); e.stopPropagation();
      const pt = svg.createSVGPoint();
      const toY = ev => { pt.x = ev.clientX; pt.y = ev.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()).y; };
      const y0 = toY(e);
      let row = startRow;
      svg.classList.add("bb-dragging");
      const guide = el("rect", { x: BX, y: rowY(row) - P / 2, width: BOARD_RIGHT - BX, height: P, fill: "var(--accent)",
                                 opacity: .25, "pointer-events": "none" }, svg);
      const label = el("text", { x: BOARD_RIGHT - 4, y: rowY(row) + 4, fill: "var(--accent)", "font-size": 11,
                                 "font-weight": 700, "text-anchor": "end", "pointer-events": "none" }, svg, "row " + row);
      const move = ev => {
        row = Math.max(min, Math.min(max, startRow + Math.round((toY(ev) - y0) / P)));
        g.setAttribute("transform", `translate(0 ${(row - startRow) * P})`);
        guide.setAttribute("y", rowY(row) - P / 2); label.setAttribute("y", rowY(row) + 4); label.textContent = "row " + row;
      };
      const up = () => {
        window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up);
        guide.remove(); label.remove();
        svg.classList.remove("bb-dragging");
        if (row !== startRow) done(row); else g.removeAttribute("transform");
      };
      window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
    });
  }

  function draw(svg, opts) {
    const layout = opts.layout || { node_columns: {}, probes: {} };
    const parts = opts.parts || [];
    espTop = (layout.esp && layout.esp.top) || 1;
    const espRow = i => rowY(espTop + i);
    svg.innerHTML = "";
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    el("style", {}, svg).textContent = `
      .bb-hole { fill: var(--hole); } .bb-click .bb-hole { cursor: pointer; }
      .bb-click .bb-hole:hover, .bb-hole.net { fill: var(--accent); }
      .bb-hole.used { opacity: .3; }
      .bb-part.future { opacity: .25; }
      .bb-part .leg { stroke: #8a8f96; }
      .bb-part.current .leg, .bb-part.current .body { stroke: var(--accent); }
      .bb-part.current .body { fill: color-mix(in srgb, var(--accent) 30%, var(--resistor)); stroke-width: 2; }
      .bb-part.current .leg, .bb-part.selected .leg { stroke-width: 2.6; }
      .bb-part.selected .leg, .bb-part.selected .body { stroke: var(--accent); }
      .bb-part.verified .leg, .bb-part.verified .body { stroke: var(--green); }
      .bb-part.pending .leg, .bb-part.pending .body { stroke: var(--yellow); }
      .bb-part.verified .leg, .bb-part.pending .leg { stroke-width: 2.3; }
      .bb-click .bb-part { cursor: pointer; }
      svg:not(.bb-click) .bb-hole, svg:not(.bb-click) .bb-part { pointer-events: none; }  /* let drags reach the rows */
      .bb-dragging .bb-wire, .bb-dragging .bb-part { opacity: .12; }
      .bb-sockres .sr-leg { stroke: #8a6d43; stroke-width: 1.8; opacity: .3; transition: stroke .3s, opacity .3s; }
      .bb-sockres .sr-body { fill: #d8b98a; fill-opacity: .4; stroke: #8a6d43; stroke-opacity: .45; stroke-width: 1.4;
                             transition: fill-opacity .3s, stroke .3s, stroke-opacity .3s, stroke-width .3s; }
      .bb-sockres .sr-text { fill: #4a3518; opacity: .85; font-size: 8.5px; font-weight: 700; transition: opacity .3s, fill .3s; }
      .bb-sockres .sr-end { fill: #8a6d43; opacity: .3; }
      .bb-sockres.present .sr-leg { stroke: #8a8f96; opacity: 1; }
      .bb-sockres.present .sr-body { fill: var(--resistor); fill-opacity: 1; stroke: #8a6d43; stroke-opacity: 1; stroke-width: 1.4; }
      .bb-sockres.present .sr-text { fill: #3b2c16; opacity: 1; }
      .bb-sockres.present .sr-end { fill: #6b7280; opacity: 1; }
      .bb-sockres.present.ok .sr-body { stroke: var(--green); stroke-width: 2.4; }
      .bb-sockres.present.bad .sr-body { stroke: var(--red); stroke-width: 2.4; }
      .bb-sockres .sr-pop { transform-box: fill-box; transform-origin: center; }
      .bb-sockres.popping .sr-pop { animation: sr-pop .35s ease-out; }
      .bb-suspect { fill: none; stroke: var(--red); stroke-width: 2.2; stroke-dasharray: 5 3; pointer-events: none;
                    animation: bb-pulse 2.4s ease-in-out infinite; }
      @keyframes bb-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
      @keyframes sr-pop { 0% { transform: scale(.75); opacity: .3; } 70% { transform: scale(1.12); } 100% { transform: scale(1); opacity: 1; } }`;
    if (opts.onHole) svg.classList.add("bb-click");

    const nodeRow = layout.node_columns || {};
    const rowNode = Object.fromEntries(Object.entries(nodeRow).map(([n, r]) => [r, n]));
    Object.entries(layout.extra_columns || {}).forEach(([n, rows]) => rows.forEach(r => rowNode[r] = n));
    const colorOf = {};
    Object.keys(nodeRow).forEach((n, i) => colorOf[n] = NODE_COLORS[i % NODE_COLORS.length]);
    const hl = new Set((opts.highlight || []).map(netOf));

    const wires = (layout.wires || []).filter(w => w.kind === "probe");

    // Board body, rails, letters and row numbers
    el("rect", { x: BX, y: 14, width: BOARD_RIGHT - BX, height: H - 22, rx: 8, fill: "var(--board)", stroke: "var(--board-edge)" }, svg);
    el("rect", { x: X.f - 1.7 * P, y: 20, width: 0.9 * P, height: H - 34, fill: "var(--board-edge)", opacity: .5 }, svg); // centre channel
    const railBand = (x, color, net) => el("rect", { x: x - 7, y: Y0 - 10, width: 14, height: rowY(NROWS) - Y0 + 20, rx: 5,
      fill: color, "fill-opacity": hl.has(net) ? .3 : .09, stroke: hl.has(net) ? "var(--accent)" : "none", "stroke-width": 2 }, svg);
    railBand(X["+"], "var(--rail-pos)", "rail+"); railBand(X["-"], "var(--rail-neg)", "rail-");
    railBand(X["R+"], "var(--rail-pos)", "railR+"); railBand(X["R-"], "var(--rail-neg)", "railR-");
    [["+", "var(--rail-pos)", "+"], ["-", "var(--rail-neg)", "−"], ["R+", "var(--rail-pos)", "+"], ["R-", "var(--rail-neg)", "−"]]
      .forEach(([k, c, t]) => el("text", { x: X[k], y: 32, fill: c, "font-size": 13, "font-weight": 700, "text-anchor": "middle" }, svg, t));
    [...ROWS, ...ROWS2].forEach(r => el("text", { x: X[r], y: 32, fill: "var(--muted)", "font-size": 10, "text-anchor": "middle" }, svg, r));
    for (let r = 1; r <= NROWS; r++) if (r === 1 || r % 5 === 0 || rowNode[r] || (layout.socket && r === layout.socket.column))
      el("text", { x: (X.e + X.f) / 2, y: rowY(r) + 3.5,  // in the centre channel, clear of the parts fill: rowNode[r] ? colorOf[rowNode[r]] : "var(--muted)",
                   "font-size": 9.5, "font-weight": rowNode[r] ? 700 : 400, "text-anchor": "middle" }, svg, r);

    // Node strips: the five holes a-e of a row are one connection, so tint the whole row
    const strip = (r, color, on) => el("rect", { x: X.a - 0.55 * P, y: rowY(r) - 0.45 * P, width: 5.1 * P, height: 0.9 * P, rx: 5,
      fill: color, "fill-opacity": on ? .3 : .13, stroke: on ? "var(--accent)" : color, "stroke-opacity": on ? 1 : .5,
      "stroke-width": on ? 2.2 : 1 }, svg);
    const tag = (r, color, text) => el("text", { x: BX - 6, y: rowY(r) + 4, fill: color, "font-size": 11,
      "font-weight": 700, "text-anchor": "end" }, svg, text);
    Object.entries(layout.extra_columns || {}).forEach(([n, rows]) => rows.forEach(r => strip(r, colorOf[n], hl.has("col" + r))));
    Object.entries(nodeRow).forEach(([n, r]) => {
      const st = strip(r, colorOf[n], hl.has("col" + r)), tg = tag(r, colorOf[n], `node ${n.toUpperCase()} ▸`);
      if (opts.onMoveNode) {
        const g = el("g", { class: "bb-drag" }, svg); g.append(st, tg);
        tg.textContent = `⇕ node ${n.toUpperCase()} ▸`;
        dragRows(svg, g, Number(r), row => opts.onMoveNode(n, row), 1, NROWS);
      }
    });
    // Rows of the nodes a failed check blames
    (opts.problemNodes || []).forEach(n => [nodeRow[n], ...((layout.extra_columns || {})[n] || [])].forEach(r => {
      if (r != null) el("rect", { x: X.a - 0.55 * P, y: rowY(r) - 0.45 * P, width: 5.1 * P, height: 0.9 * P, rx: 5,
                                  fill: "var(--red)", "fill-opacity": .22, stroke: "var(--red)", "stroke-opacity": .55,
                                  "stroke-width": 1.4, "pointer-events": "none" }, svg);
    }));
    const sock = layout.socket;
    if (sock) { strip(sock.column, "#6b7280", !!opts.socketActive); tag(sock.column, "var(--muted)", "test socket ▸"); }
    hl.forEach(net => {
      if (!net.startsWith("col") || net.startsWith("colR")) return;
      const r = Number(net.slice(3));
      if (!rowNode[r] && !(sock && r === sock.column)) strip(r, "var(--accent)", true);
    });

    // Holes
    const used = opts.usedHoles || new Set();
    for (let r = 1; r <= NROWS; r++) for (const k of ["+", "-", ...ROWS, ...ROWS2, "R+", "R-"]) {
      const id = `${k}:${r}`, [x, y] = holeXY(id);
      const h = el("rect", { x: x - 3.6, y: y - 3.6, width: 7.2, height: 7.2, rx: 1.5, class: "bb-hole" + (used.has(id) ? " used" : "") }, svg);
      if (id === opts.firstHole) h.setAttribute("style", "fill: var(--accent)");
      if (opts.onHole) {
        h.dataset.net = netOf(id);
        h.addEventListener("click", () => opts.onHole(id));
        h.addEventListener("mouseenter", () => svg.querySelectorAll(`.bb-hole[data-net="${h.dataset.net}"]`).forEach(o => o.classList.add("net")));
        h.addEventListener("mouseleave", () => svg.querySelectorAll(".bb-hole.net").forEach(o => o.classList.remove("net")));
      }
    }

    // Parts: straight, at real size (a 1/4 W resistor body is about 2.4 hole pitches long)
    parts.forEach(p => {
      const g = el("g", { class: `bb-part ${p.state || "done"}` }, svg);
      if (opts.onPart) g.addEventListener("click", e => { e.stopPropagation(); opts.onPart(p.id); });
      const [x1, y1] = holeXY(p.a), [x2, y2] = holeXY(p.b);
      const dash = p.loose ? "4 3" : "none";
      if (p.kind === "R") {
        const L = Math.hypot(x2 - x1, y2 - y1), ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI;
        const body = Math.max(10, Math.min(2.4 * P, L - 10));
        el("line", { x1, y1, x2, y2, "stroke-width": 1.8, class: "leg", "stroke-dasharray": dash }, g);
        const bg = el("g", { transform: `translate(${(x1 + x2) / 2},${(y1 + y2) / 2}) rotate(${ang})` }, g);
        el("rect", { x: -body / 2, y: -5.5, width: body, height: 11, rx: 5, fill: "var(--resistor)", stroke: "#8a6d43",
                     "stroke-width": 1.2, class: "body" }, bg);
        el("text", { x: 0, y: 3, "font-size": 8, "font-weight": 700, "text-anchor": "middle", fill: "#3b2c16",
                     transform: Math.abs(ang) > 90 ? "rotate(180)" : "" }, bg, p.label || fmtOhms(p.value));
        if (p.name) {
          const vertical = Math.abs(x2 - x1) < 1;
          el("text", { x: (x1 + x2) / 2 + (vertical ? 9 : 0), y: (y1 + y2) / 2 + (vertical ? 3 : -9),
                       "font-size": 9, "font-weight": 700, fill: "var(--ink)", "text-anchor": vertical ? "start" : "middle" }, g, p.name);
        }
      } else {
        const color = p.kind === "P3" ? "var(--rail-pos)" : p.kind === "PG" ? "#30363d" : "var(--green)";
        const d = p.kind === "W" ? `M${x1},${y1} Q${Math.max(x1, x2) + 14 + Math.min(Math.abs(y2 - y1) * 0.25, 40)},${(y1 + y2) / 2} ${x2},${y2}`
                                 : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
        el("path", { d, fill: "none", stroke: p.state === "current" ? "var(--accent)" : color,
                     "stroke-width": p.state === "current" ? 3.2 : 2.5, "stroke-dasharray": dash, "stroke-linecap": "round",
                     opacity: p.state === "future" ? .25 : 1 }, g);
      }
      [[x1, y1], [x2, y2]].forEach(([x, y]) => el("circle", { cx: x, cy: y, r: 2.8, fill: "#6b7280" }, g));
      if (p.suspect) {
        if (p.kind === "R") el("rect", { x: Math.min(x1, x2) - 9, y: Math.min(y1, y2) - 9, width: Math.abs(x2 - x1) + 18,
                                         height: Math.abs(y2 - y1) + 18, rx: 8, class: "bb-suspect" }, g);
        else [[x1, y1], [x2, y2]].forEach(([x, y]) => el("circle", { cx: x, cy: y, r: 9, class: "bb-suspect" }, g));
      }
    });

    // The ESP32 on the right half, and its measuring wires: from the pin's row (hole f) to the node
    const espG = el("g", { class: "bb-esp" }, svg);
    el("rect", { x: ESP_LEFT, y: espRow(0) - 2.4 * P, width: ESP_W, height: espRow(ESP_HEADER.length - 1) - espRow(0) + 6 * P,
                 rx: 7, fill: "var(--esp)", opacity: .94 }, espG);
    el("text", { x: ESP_LEFT + ESP_W / 2, y: espRow(0) - 1.1 * P, fill: "#fff", "font-size": 11, "font-weight": 700,
                 "text-anchor": "middle" }, espG, opts.onMoveEsp ? "ESP32-S3 (drag)" : "ESP32-S3");
    el("text", { x: ESP_LEFT + ESP_W / 2, y: espRow(ESP_HEADER.length - 1) + 2.6 * P, fill: "#9aa3ad", "font-size": 9,
                 "text-anchor": "middle" }, espG, "USB");
    const usedPins = new Set(["3V3", "GND", ...wires.map(w => w.pin)]);
    const MEASURING = new Set(["4", "5", "6", "7", "8", "9", "10"]);   // ADC1: the only pins that measure with Wi-Fi on
    ESP_HEADER.forEach((name, i) => {
      const y = espRow(i), on = usedPins.has(name), canMeasure = MEASURING.has(name);
      el("circle", { cx: X.j, cy: y, r: 3.4, fill: on ? "#e7c76a" : canMeasure ? "#9aa3ad" : "#4b5563" }, espG);
      el("text", { x: X.j + 9, y: y + 3.5, fill: on ? "#fff" : canMeasure ? "#cfd6de" : "#6b7280",
                   "font-size": 9, "font-weight": on ? 700 : 400 },
         espG, /^\d+$/.test(name) ? "GPIO " + name : name);
    });
    el("text", { x: ESP_LEFT + ESP_W / 2, y: espRow(ESP_HEADER.length - 1) + 1.6 * P, fill: "#9aa3ad", "font-size": 8,
                 "text-anchor": "middle" }, espG, "only GPIO 4–10 can measure with Wi-Fi on");
    if (opts.onMoveEsp) dragRows(svg, espG, espTop, r => opts.onMoveEsp(r), 1, NROWS - ESP_HEADER.length + 1);
    wires.forEach(w => {
      const [x1, y1] = holeXY(w.from), [x2, y2] = holeXY(w.to);
      el("line", { x1, y1, x2, y2, stroke: opts.probesCurrent ? "var(--accent)" : "#e0b000", "stroke-width": 2.4,
                   "stroke-linecap": "round", "pointer-events": "none", class: "bb-wire" }, svg);
      el("circle", { cx: x1, cy: y1, r: 2.8, fill: "#6b7280", class: "bb-wire" }, svg);
    });

    // The test socket's resistor: a faded brown ghost while nothing is plugged in (labelled with
    // the value this step wants), a solid resistor with its value once one is (BB.setSocketPart).
    if (sock && opts.socketHelp) {
      const [x1, y1] = holeXY(sock.test[0]), [x2, y2] = holeXY(sock.test[1]);
      const ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI, flip = Math.abs(ang) > 90;
      const half = Math.hypot(x2 - x1, y2 - y1) / 2, body = Math.min(2.6 * P, 2 * half - 6);
      const g = el("g", { class: "bb-sockres", id: "bb-sockres", "pointer-events": "none" }, svg);
      const bg = el("g", { transform: `translate(${(x1 + x2) / 2},${(y1 + y2) / 2}) rotate(${flip ? ang + 180 : ang})` }, g);
      // the legs stop at the body, so no line runs through it
      el("line", { x1: -half, y1: 0, x2: -body / 2, y2: 0, class: "sr-leg" }, bg);
      el("line", { x1: body / 2, y1: 0, x2: half, y2: 0, class: "sr-leg" }, bg);
      const pop = el("g", { class: "sr-pop" }, bg);
      el("rect", { x: -body / 2, y: -6.5, width: body, height: 13, rx: 6, class: "sr-body" }, pop);
      const ghost = opts.socketExpect || "";
      el("text", { x: 0, y: 3.2, "text-anchor": "middle", class: "sr-text", "data-ghost": ghost }, pop, ghost);
      [[x1, y1], [x2, y2]].forEach(([x, y]) => el("circle", { cx: x, cy: y, r: 2.6, class: "sr-end" }, g));
    }

    // Measuring wire ends: a ring in the hole and a tag with the GPIO number
    wires.map(w => [w.pin, w.to]).forEach(([p, hole]) => {
      const [x, y] = holeXY(hole);
      const c = opts.probesCurrent ? "var(--accent)" : "#e0b000";
      const g = el("g", { "pointer-events": "none", class: "bb-wire" }, svg);
      el("circle", { cx: x, cy: y, r: 6, fill: "none", stroke: c, "stroke-width": 2.2 }, g);
      el("rect", { x: x + 5, y: y - 14, width: 18, height: 10, rx: 3, fill: c }, g);
      el("text", { x: x + 14, y: y - 6.5, "font-size": 8, "font-weight": 700, fill: "#fff", "text-anchor": "middle" }, g, p);
    });
  }

  // Switch the socket's resistor between placeholder and real. label: the measured value shown on
  // it; tone: "ok" (green outline), "bad" (red) or "" (neutral). It pops in once, when it appears.
  let socketShown = false;   // survives redraws, so a redrawn board does not replay the pop
  function setSocketPart(svg, present, label, tone) {
    const g = svg.querySelector("#bb-sockres");
    if (!g) return;
    const t = g.querySelector(".sr-text");
    const was = socketShown;
    socketShown = present;
    g.classList.toggle("present", present);
    g.classList.toggle("ok", present && tone === "ok");
    g.classList.toggle("bad", present && tone === "bad");
    t.textContent = present ? label : t.getAttribute("data-ghost");
    if (present && !was) {
      g.classList.remove("popping"); void g.getBBox(); g.classList.add("popping");
    }
  }

  // For zooming: the whole drawing, and the y position of a row.
  const size = () => ({ W, H });
  return { NROWS, ROWS, P, holeXY, netOf, holeName, fmtOhms, draw, rowY, size, setSocketPart, espRows: () => [espTop, espTop + ESP_HEADER.length - 1] };
})();
