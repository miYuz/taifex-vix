
const SVGNS = "http://www.w3.org/2000/svg";
const el = (n, a = {}) => {
  const e = document.createElementNS(SVGNS, n);
  for (const k in a) if (a[k] !== null && a[k] !== undefined) e.setAttribute(k, a[k]);
  return e;
};
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmt = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : v.toFixed(n);

// 30 天只作校準用(對照官方指數),不畫在圖上
const SERIES = [
  { key: "v7",  name: "VIX 7天",  color: "--s1", short: "7天"  },
  { key: "v14", name: "VIX 14天", color: "--s2", short: "14天" },
];

const N = DATA.d.length;
let view = { from: 0, to: N };            // [from, to) 索引區間

const KEYS = ["d", "v7", "v14", "v30", "m7", "m14", "s7", "s14", "ba", "of", "px"];
const slice = () => {
  const o = {};
  for (const k of KEYS) o[k] = DATA[k].slice(view.from, view.to);
  return o;
};

// 年度 → [起始索引, 結束索引) ,給年度按鈕用
const YEARS = (() => {
  const m = new Map();
  DATA.d.forEach((iso, i) => {
    const y = iso.slice(0, 4);
    if (!m.has(y)) m.set(y, [i, i + 1]);
    else m.get(y)[1] = i + 1;
  });
  return m;
})();

/* 三張圖共用左右邊界,繪圖區才會嚴格對齊(游標線要貫穿,對齊是前提)。
   各自寫死的話很容易在改動時漂掉。 */
const marginsFor = (W) => ({ l: 46, r: W < 560 ? 16 : 60 });

/* ---------- 刻度 ---------- */
function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const start = Math.ceil(lo / step) * step;
  const out = [];
  for (let v = start; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

function dateTicks(dates, maxN) {
  const n = dates.length;
  const want = Math.max(2, Math.min(maxN, 7));
  const step = Math.max(1, Math.round(n / want));
  const idx = [];
  for (let i = 0; i < n; i += step) idx.push(i);
  if (idx[idx.length - 1] !== n - 1) idx.push(n - 1);
  return idx;
}

function labelFor(iso, dense) {
  return dense ? iso.slice(5) : iso.slice(0, 7);
}

/* ---------- 主圖 ---------- */
function drawMain(host, S) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const W = Math.max(320, host.clientWidth);
  const PLOT_H = W < 560 ? 210 : 290;
  const M = { t: 14, b: 26, ...marginsFor(W) };
  const H = PLOT_H + M.t + M.b;
  const iw = W - M.l - M.r, ih = PLOT_H;

  const vals = [];
  for (const s of SERIES) for (const v of S[s.key]) if (v !== null) vals.push(v);
  const lo0 = Math.min(...vals), hi0 = Math.max(...vals);
  const pad = (hi0 - lo0) * 0.08 || 1;
  const lo = Math.max(0, lo0 - pad), hi = hi0 + pad;

  const n = S.d.length;
  const X = (i) => M.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                          role: "img", "aria-label": "固定天期 VIX 時間序列" });

  // 格線 + y 軸
  for (const t of niceTicks(lo, hi, 5)) {
    svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: Y(t), y2: Y(t),
                                 stroke: css("--grid"), "stroke-width": 1 }));
    const tx = el("text", { x: M.l - 8, y: Y(t) + 4, "text-anchor": "end",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = t.toFixed(0);
    svg.appendChild(tx);
  }
  // x 軸
  svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih,
                               stroke: css("--axis"), "stroke-width": 1 }));
  const dense = n <= 90;
  for (const i of dateTicks(S.d, Math.floor(iw / 74))) {
    const tx = el("text", { x: X(i), y: M.t + ih + 17, "text-anchor": "middle",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = labelFor(S.d[i], dense);
    svg.appendChild(tx);
  }

  // 線
  const ends = [];
  for (const s of SERIES) {
    let dpath = "", open = false, lastI = -1;
    S[s.key].forEach((v, i) => {
      if (v === null) { open = false; return; }
      dpath += (open ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
      open = true; lastI = i;
    });
    svg.appendChild(el("path", { d: dpath, fill: "none", stroke: css(s.color),
                                 "stroke-width": 2, "stroke-linejoin": "round",
                                 "stroke-linecap": "round" }));
    if (lastI >= 0) ends.push({ s, i: lastI, v: S[s.key][lastI], y: Y(S[s.key][lastI]) });
  }

  // 端點直接標註(對比不足的顏色靠這個補足可讀性)
  if (M.r > 30) {
    ends.sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++)
      if (ends[k].y - ends[k - 1].y < 13) ends[k].y = ends[k - 1].y + 13;
    for (const e of ends) {
      svg.appendChild(el("circle", { cx: X(e.i), cy: Y(e.v), r: 3.5,
                                     fill: css(e.s.color), stroke: css("--surface"),
                                     "stroke-width": 2 }));
      const tx = el("text", { x: M.l + iw + 8, y: e.y + 4, fill: css("--ink-2"),
                              "font-size": 11.5,
                              "font-family": "ui-monospace, Consolas, monospace" });
      tx.textContent = fmt(e.v, 1);
      svg.appendChild(tx);
    }
  }

  // 十字線
  const cross = el("line", { y1: M.t, y2: M.t + ih, stroke: css("--axis"),
                             "stroke-width": 1, opacity: 0 });
  svg.appendChild(cross);
  const dots = SERIES.map((s) => ({
    el: svg.appendChild(el("circle", { r: 4, fill: css(s.color),
                                       stroke: css("--surface"),
                                       "stroke-width": 2, opacity: 0 })),
    get: (i) => S[s.key][i],
  }));

  host.appendChild(svg);
  return { svg, X, Y, M, iw, ih, n, W, cross, dots, host };
}

/* ---------- 價差圖 ---------- */
function drawSpread(host, S) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const W = Math.max(320, host.clientWidth);
  const PLOT_H = W < 560 ? 118 : 150;
  const M = { t: 12, b: 26, ...marginsFor(W) };
  const H = PLOT_H + M.t + M.b;
  const iw = W - M.l - M.r, ih = PLOT_H;

  const sp = S.v7.map((a, i) => (a === null || S.v14[i] === null) ? null : a - S.v14[i]);
  const vals = sp.filter((v) => v !== null);
  const mx = Math.max(Math.abs(Math.min(...vals)), Math.abs(Math.max(...vals))) * 1.1 || 1;
  const n = sp.length;
  const X = (i) => M.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v) => M.t + ih / 2 - (v / mx) * (ih / 2);

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                          role: "img", "aria-label": "VIX7 減 VIX14 期限結構價差" });

  for (const t of [mx * 0.6, -mx * 0.6]) {
    svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: Y(t), y2: Y(t),
                                 stroke: css("--grid"), "stroke-width": 1 }));
    const tx = el("text", { x: M.l - 8, y: Y(t) + 4, "text-anchor": "end",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = (t > 0 ? "+" : "") + t.toFixed(1);
    svg.appendChild(tx);
  }

  // 依 NaN 切成連續段。每一段都必須自己「從零線出發、回到零線收尾」:
  // 若只在最後一段補收尾,前面每一段都會被 SVG 的 implicit close 用一條對角線
  // 從段末端拉回段起點,橫跨整段寬度 —— 圖看起來就是歪的。
  const segs = [];
  let cur = [];
  sp.forEach((v, i) => {
    if (v === null) { if (cur.length) segs.push(cur); cur = []; }
    else cur.push([i, v]);
  });
  if (cur.length) segs.push(cur);

  const zero = Y(0);
  const areaPath = segs.map((seg) => {
    const head = `M${X(seg[0][0]).toFixed(1)} ${zero.toFixed(1)} `;
    const body = seg.map(([i, v]) => `L${X(i).toFixed(1)} ${Y(v).toFixed(1)} `).join("");
    const tail = `L${X(seg[seg.length - 1][0]).toFixed(1)} ${zero.toFixed(1)} Z `;
    return head + body + tail;
  }).join("");

  // 正負分開填，兩色各自 clip 到零線的一側
  for (const [id, color, above] of [["clipPos", "--pos", true], ["clipNeg", "--neg", false]]) {
    const cp = el("clipPath", { id });
    cp.appendChild(el("rect", { x: M.l, y: above ? M.t : zero,
                                width: iw, height: above ? zero - M.t : M.t + ih - zero }));
    svg.appendChild(cp);
    if (areaPath) {
      svg.appendChild(el("path", { d: areaPath, fill: css(color), opacity: 0.5,
                                   "clip-path": `url(#${id})` }));
    }
  }

  svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: zero, y2: zero,
                               stroke: css("--axis"), "stroke-width": 1 }));
  const zl = el("text", { x: M.l - 8, y: zero + 4, "text-anchor": "end",
                          fill: css("--muted"), "font-size": 11,
                          "font-family": "ui-monospace, Consolas, monospace" });
  zl.textContent = "0";
  svg.appendChild(zl);

  const dense = n <= 90;
  for (const i of dateTicks(S.d, Math.floor(iw / 74))) {
    const tx = el("text", { x: X(i), y: M.t + ih + 17, "text-anchor": "middle",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = labelFor(S.d[i], dense);
    svg.appendChild(tx);
  }

  const cross = el("line", { y1: M.t, y2: M.t + ih, stroke: css("--axis"),
                             "stroke-width": 1, opacity: 0 });
  svg.appendChild(cross);
  const dot = svg.appendChild(el("circle", { r: 4, fill: css("--ink-2"),
                                             stroke: css("--surface"),
                                             "stroke-width": 2, opacity: 0 }));

  host.appendChild(svg);
  return { svg, X, Y, M, iw, ih, n, W, cross, host,
           dots: [{ el: dot, get: (i) => sp[i] }], sp };
}

/* ---------- 0050 收盤價(獨立座標軸) ---------- */
function drawPrice(host, S) {
  host.querySelectorAll("svg").forEach((n) => n.remove());
  const W = Math.max(320, host.clientWidth);
  const PLOT_H = W < 560 ? 130 : 170;
  const M = { t: 12, b: 26, ...marginsFor(W) };
  const H = PLOT_H + M.t + M.b;
  const iw = W - M.l - M.r, ih = PLOT_H;

  const vals = S.px.filter((v) => v !== null);
  if (!vals.length) return null;
  const lo0 = Math.min(...vals), hi0 = Math.max(...vals);
  const pad = (hi0 - lo0) * 0.08 || 1;
  const lo = lo0 - pad, hi = hi0 + pad;

  const n = S.px.length;
  const X = (i) => M.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v) => M.t + ih - ((v - lo) / (hi - lo)) * ih;

  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H,
                          role: "img", "aria-label": "0050 收盤價" });

  for (const t of niceTicks(lo, hi, 4)) {
    svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: Y(t), y2: Y(t),
                                 stroke: css("--grid"), "stroke-width": 1 }));
    const tx = el("text", { x: M.l - 8, y: Y(t) + 4, "text-anchor": "end",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = t.toFixed(0);
    svg.appendChild(tx);
  }
  svg.appendChild(el("line", { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih,
                               stroke: css("--axis"), "stroke-width": 1 }));

  let dpath = "", open = false, lastI = -1;
  S.px.forEach((v, i) => {
    if (v === null) { open = false; return; }
    dpath += (open ? "L" : "M") + X(i).toFixed(1) + " " + Y(v).toFixed(1) + " ";
    open = true; lastI = i;
  });
  svg.appendChild(el("path", { d: dpath, fill: "none", stroke: css("--s3"),
                               "stroke-width": 2, "stroke-linejoin": "round",
                               "stroke-linecap": "round" }));

  if (lastI >= 0 && M.r > 30) {
    svg.appendChild(el("circle", { cx: X(lastI), cy: Y(S.px[lastI]), r: 3.5,
                                   fill: css("--s3"), stroke: css("--surface"),
                                   "stroke-width": 2 }));
    const tx = el("text", { x: M.l + iw + 8, y: Y(S.px[lastI]) + 4,
                            fill: css("--ink-2"), "font-size": 11.5,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = fmt(S.px[lastI], 1);
    svg.appendChild(tx);
  }

  const dense = n <= 90;
  for (const i of dateTicks(S.d, Math.floor(iw / 74))) {
    const tx = el("text", { x: X(i), y: M.t + ih + 17, "text-anchor": "middle",
                            fill: css("--muted"), "font-size": 11,
                            "font-family": "ui-monospace, Consolas, monospace" });
    tx.textContent = labelFor(S.d[i], dense);
    svg.appendChild(tx);
  }

  const cross = el("line", { y1: M.t, y2: M.t + ih, stroke: css("--axis"),
                             "stroke-width": 1, opacity: 0 });
  svg.appendChild(cross);
  const dot = svg.appendChild(el("circle", { r: 4, fill: css("--s3"),
                                             stroke: css("--surface"),
                                             "stroke-width": 2, opacity: 0 }));

  host.appendChild(svg);
  return { svg, X, Y, M, iw, ih, n, W, cross, host,
           dots: [{ el: dot, get: (i) => S.px[i] }] };
}

/* ---------- 卡片 ---------- */
function drawTiles(S) {
  const i = S.d.length - 1;
  const spread = (S.v7[i] === null || S.v14[i] === null) ? null : S.v7[i] - S.v14[i];
  const items = [
    { k: "VIX 7天",  c: "--s1", v: S.v7[i],
      sub: `模式 ${S.m7[i]}・跨度 ${S.s7[i] === null ? "—" : S.s7[i] + " 天"}` },
    { k: "VIX 14天", c: "--s2", v: S.v14[i], sub: `模式 ${S.m14[i]}` },
    { k: "7天 - 14天", c: null, v: spread,
      sub: spread === null ? "—" : (spread > 0 ? "短天期較貴（倒掛）" : "正價差") },
    { k: "0050 收盤", c: "--s3", v: S.px[i], sub: "已還原分割" },
  ];
  document.getElementById("tiles").innerHTML = items.map((it) => `
    <div class="tile">
      <div class="k">${it.c ? `<span class="swatch" style="background:var(${it.c})"></span>` : ""}${it.k}</div>
      <div class="v">${it.v === null ? "—" : (it.k.includes("-") && it.v > 0 ? "+" : "") + fmt(it.v)}</div>
      <div class="sub">${it.sub}</div>
    </div>`).join("");
  document.querySelector("header .lede").insertAdjacentHTML;
}

function drawTable(S) {
  const rows = [];
  for (let i = S.d.length - 1; i >= 0 && rows.length < 15; i--) {
    const sp = (S.v7[i] === null || S.v14[i] === null) ? null : S.v7[i] - S.v14[i];
    rows.push(`<tr>
      <td>${S.d[i]}</td>
      <td class="${S.v7[i] === null ? "na" : ""}">${fmt(S.v7[i])}</td>
      <td class="${S.v14[i] === null ? "na" : ""}">${fmt(S.v14[i])}</td>
      <td class="${S.px[i] === null ? "na" : ""}">${fmt(S.px[i])}</td>
      <td class="${sp === null ? "na" : ""}">${sp === null ? "—" : (sp > 0 ? "+" : "") + fmt(sp)}</td>
      <td><span class="badge">${S.m7[i]}</span></td>
      <td class="${S.s7[i] !== null && S.s7[i] > 14 ? "na" : ""}">${S.s7[i] === null ? "—" : S.s7[i] + " 天"}</td>
      <td>${S.ba[i] === null ? "—" : (S.ba[i] * 100).toFixed(0) + "%"}</td>
    </tr>`);
  }
  document.getElementById("tbody").innerHTML = rows.join("");
  document.querySelector("caption").textContent =
    `區間最後 ${rows.length} 個交易日（完整資料在 D:\\taifex_vix\\vix_txo_daily.csv）`;
}

/* ---------- 互動:游標線貫穿三張圖 ---------- */
function tipHTML(S, sp, i) {
  const rows = SERIES.map((s) => `
    <div class="row"><span class="swatch" style="background:var(${s.color})"></span>${s.short}
    <b>${fmt(S[s.key][i])}</b></div>`).join("");
  const span = S.s7[i];
  const q = span === null ? "" : span <= 7 ? "窄" : span <= 14 ? "中" : "寬";
  return `<div class="date">${S.d[i]}</div>${rows}
    <div class="row">7-14
      <b>${sp[i] === null ? "—" : (sp[i] > 0 ? "+" : "") + fmt(sp[i])}</b></div>
    <div class="row" style="margin-top:4px">
      <span class="swatch" style="background:var(--s3)"></span>0050
      <b>${fmt(S.px[i])}</b></div>
    <div class="mode">7天 ${S.m7[i]} · 插值跨度 ${span === null ? "—" : span + " 天（" + q + "）"}</div>`;
}

function bindCrosshair(charts, S, sp) {
  const live = charts.filter(Boolean);
  const tips = live.map((c) => c.host.querySelector(".tip"));

  const hide = () => {
    live.forEach((c, k) => {
      c.cross.setAttribute("opacity", 0);
      c.dots.forEach((d) => d.el.setAttribute("opacity", 0));
      if (tips[k]) tips[k].classList.remove("on");
    });
  };

  // 三張圖共用同一個索引 i,所以線一定落在同一個交易日上
  const show = (i, activeIdx) => {
    const html = tipHTML(S, sp, i);
    live.forEach((c, k) => {
      const px = c.X(i);
      c.cross.setAttribute("x1", px);
      c.cross.setAttribute("x2", px);
      c.cross.setAttribute("opacity", 1);
      c.dots.forEach((d) => {
        const v = d.get(i);
        if (v === null || v === undefined) { d.el.setAttribute("opacity", 0); return; }
        d.el.setAttribute("cx", px);
        d.el.setAttribute("cy", c.Y(v));
        d.el.setAttribute("opacity", 1);
      });
      const tip = tips[k];
      if (!tip) return;
      if (k !== activeIdx) { tip.classList.remove("on"); return; }
      tip.innerHTML = html;
      tip.classList.add("on");
      const scale = c.host.clientWidth / c.W;
      const tw = tip.offsetWidth;
      const left = Math.max(tw / 2 + 2,
                            Math.min(c.host.clientWidth - tw / 2 - 2, px * scale));
      tip.style.left = left + "px";
      tip.style.top = "6px";
    });
  };

  live.forEach((c, k) => {
    const idxFrom = (clientX) => {
      const r = c.svg.getBoundingClientRect();
      const x = (clientX - r.left) * (c.W / r.width);
      const t = (x - c.M.l) / c.iw;
      return Math.max(0, Math.min(c.n - 1, Math.round(t * (c.n - 1))));
    };
    c.svg.addEventListener("pointermove", (e) => show(idxFrom(e.clientX), k));
    c.svg.addEventListener("pointerdown", (e) => show(idxFrom(e.clientX), k));
    c.svg.addEventListener("pointerleave", hide);
  });
}

/* ---------- 主流程 ---------- */
function medianSpan(S) {
  const v = S.s7.filter((x) => x !== null).sort((a, b) => a - b);
  if (!v.length) return null;
  return v[Math.floor(v.length / 2)];
}

function render() {
  const S = slice();
  const ms = medianSpan(S);
  document.getElementById("rangeNote").textContent =
    `${S.d[0]} ~ ${S.d[S.d.length - 1]}，${S.d.length} 個交易日` +
    (ms === null ? "" : ` · 7天插值跨度中位 ${ms} 天` +
      (ms <= 7 ? "（窄）" : ms <= 14 ? "（中）" : "（寬，誤差較大）"));
  drawTiles(S);
  const cMain = drawMain(document.getElementById("plotMain"), S);
  const cPrice = drawPrice(document.getElementById("plotPrice"), S);
  const cSpread = drawSpread(document.getElementById("plotSpread"), S);
  bindCrosshair([cMain, cPrice, cSpread], S, cSpread.sp);
  drawTable(S);
}

// 年度按鈕(依資料自動生成,不寫死年份)
const yearRow = document.getElementById("yearRow");
for (const [y, [a, b]] of YEARS) {
  const btn = document.createElement("button");
  btn.className = "range";
  btn.dataset.from = a;
  btn.dataset.to = b;
  btn.setAttribute("aria-pressed", "false");
  btn.textContent = y;
  yearRow.appendChild(btn);
}

// 兩組互斥:選了年度就取消區間,反之亦然
function selectButton(b) {
  document.querySelectorAll("button.range")
    .forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
  if (b.dataset.from !== undefined) {
    view.from = +b.dataset.from;
    view.to = +b.dataset.to;
  } else {
    const days = +b.dataset.days;
    view.from = days === 0 ? 0 : Math.max(0, N - days);
    view.to = N;
  }
  render();
}

document.querySelectorAll("button.range")
  .forEach((b) => b.addEventListener("click", () => selectButton(b)));

let rt;
new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(render, 90); })
  .observe(document.getElementById("plotMain"));

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement,
  { attributes: true, attributeFilter: ["data-theme"] });

render();
