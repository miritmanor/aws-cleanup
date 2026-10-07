"""The overview panel's script for the HTML report: a format VALUE, so braces are
not doubled. It computes nothing and filters nothing outside the panel."""

BUBBLES_JS = """
(function () {
  const DATA = BUBBLES;
  if (!DATA || !DATA.views || !DATA.views.length) {
    const wrap = document.getElementById("bubblesWrap");
    if (wrap) wrap.style.display = "none";
    return;
  }

  const S = 1000;                 // unit space -> viewBox units
  const chart = document.getElementById("bubblesChart");
  const listEl = document.getElementById("bubblesList");
  const detailEl = document.getElementById("bubblesDetail");
  const searchEl = document.getElementById("bubblesSearch");
  const viewsEl = document.getElementById("bubbleViews");
  const metricsEl = document.getElementById("bubbleMetrics");
  const notesEl = document.getElementById("bubblesNotes");
  const scaleEl = document.getElementById("bubblesScale");

  const VIEWS = new Map(DATA.views.map(v => [v.id, v]));
  let view = VIEWS.get(DATA.default_view) || DATA.views[0];
  let byId = new Map();
  let metric = DATA.default_metric;
  let selected = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g,
      c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  }

  // Projects: a colour hashed from the id, stable across metrics. Services and
  // bill lines: the colour of their AWS category, as everywhere else in the report.
  const PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#b07aa1", "#76b7b2",
                   "#e15759", "#9c755f", "#edc948", "#8cd17d", "#d37295"];
  function colourOf(item) {
    if (item.resource_types) {
      const meta = SERVICE_META[item.resource_types[0]];
      return meta ? meta.color : "#8c96a3";
    }
    let h = 0;
    for (let i = 0; i < item.id.length; i++) h = (h * 31 + item.id.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  const fmtInt = n => n.toLocaleString();
  function fmtMoney(amount, currency) {
    if (amount === null || amount === undefined) return "-";
    const n = Number(amount);
    return (Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(2)) + " " + (currency || "");
  }
  function fmtPct(p) { return p === null || p === undefined ? "-" : p.toFixed(0) + "%"; }

  function metricLabel(id) {
    const m = view.metrics.find(x => x[0] === id);
    return m ? m[1] : id;
  }
  function shortValue(p, m) {
    if (m === "resource_count") return fmtInt(p.resource_count);
    if (m === "unused_pct") return fmtPct(p.unused_pct);
    if (m === "unused_count") return fmtInt(p.unused);
    return fmtMoney(p.cost, "");
  }
  function valueText(p, m) {
    if (m === "resource_count") return fmtInt(p.resource_count) + " resources";
    if (m === "unused_pct") return fmtPct(p.unused_pct) + " unused";
    if (m === "unused_count") return fmtInt(p.unused) + " unused";
    return fmtMoney(p.cost, DATA.currency);
  }
  function metricAvailable(m) { return (view.packs[m] || []).length > 0; }
  function heading() { return "Overview " + view.label.toLowerCase() + " - " + metricLabel(metric); }

  // ---- the chart -----------------------------------------------------------

  function drawChart() {
    const bubbles = view.packs[metric] || [];
    if (!bubbles.length) {
      chart.innerHTML = '<div class="bubbles-empty">' + esc(unavailableReason()) + "</div>";
      return;
    }
    const parts = ['<svg viewBox="0 0 ' + S + " " + S + '" role="img" aria-label="' +
                   esc(heading()) + '" id="bubblesSvg">'];
    for (const b of bubbles) {
      const p = byId.get(b.id);
      if (!p) continue;
      const cx = (b.x * S).toFixed(2), cy = (b.y * S).toFixed(2), r = (b.r * S).toFixed(2);
      parts.push('<g class="bubble' + (selected === b.id ? " sel" : "") +
        '" tabindex="0" role="button" data-id="' + esc(b.id) +
        '" aria-label="' + esc(p.display_name + ", " + valueText(p, metric)) + '">');
      parts.push('<circle class="bub" cx="' + cx + '" cy="' + cy + '" r="' + r +
                 '" fill="' + colourOf(p) + '"></circle>');
      // Labels only where they fit; the list beside the chart names everything.
      const px = b.r * S;
      const name = p.display_name;
      const fit = Math.min(px * 0.42, (px * 1.7) / Math.max(name.length, 1));
      if (fit >= 9) {
        parts.push('<text x="' + cx + '" y="' + (Number(cy) - (px > 46 ? 3 : -fit * 0.35)).toFixed(2) +
                   '" font-size="' + fit.toFixed(1) + '">' + esc(name) + "</text>");
        if (px > 46) {
          parts.push('<text class="val" x="' + cx + '" y="' + (Number(cy) + fit * 1.15).toFixed(2) +
                     '" font-size="' + (fit * 0.85).toFixed(1) + '">' +
                     esc(shortValue(p, metric)) + "</text>");
        }
      }
      parts.push("</g>");
    }
    parts.push("</svg>");
    chart.innerHTML = parts.join("");

    chart.querySelectorAll("g.bubble").forEach(g => {
      const id = g.dataset.id;
      g.addEventListener("click", () => select(id));
      g.addEventListener("mouseenter", () => showDetail(id));
      g.addEventListener("focus", () => showDetail(id));
      g.addEventListener("blur", () => showDetail(selected));
      g.addEventListener("mouseleave", () => showDetail(selected));
      g.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(id); }
      });
    });
  }

  function unavailableReason() {
    if (metric === "cost" && !DATA.cost_queried) {
      return "Cost was not queried for this scan, so there is nothing to size " +
             "circles by. Scan again with the cost lookup on.";
    }
    if (metric === "unused_pct" || metric === "unused_count") {
      return "Nothing in this account is flagged as potentially unused, so " +
             "there is nothing to draw. That is not the same as everything " +
             "being in use - check the list for usage that is unknown.";
    }
    return "Nothing has a value for this view.";
  }

  // ---- the size legend ----------------------------------------------------

  function drawScale() {
    const bubbles = view.packs[metric] || [];
    if (!bubbles.length) { scaleEl.innerHTML = ""; return; }
    const max = bubbles[0];
    const chosen = [max, bubbles[Math.floor(bubbles.length / 2)], bubbles[bubbles.length - 1]]
      .filter((b, i, a) => b && a.indexOf(b) === i);
    const px = b => Math.max(6, (b.r / max.r) * 34);
    const swatches = chosen.map(b => {
      const d = px(b).toFixed(0);
      return '<span><i class="sc" style="width:' + d + "px;height:" + d + 'px"></i> ' +
             esc(shortValue(byId.get(b.id), metric)) + "</span>";
    }).join("");
    scaleEl.innerHTML = "<span>Area = " + esc(metricLabel(metric).toLowerCase()) +
      ". </span>" + swatches +
      '<span style="margin-left:auto">Sizes are comparable within this view only. ' +
      esc(periodText()) + "</span>";
  }

  function periodText() {
    if (metric !== "cost" || !DATA.cost_queried) return "";
    return "Billing period " + DATA.period_start + " to " + DATA.period_end + ".";
  }

  // ---- the companion list -------------------------------------------------

  function drawList() {
    const q = (searchEl.value || "").toLowerCase();
    const shown = (view.lists[metric] || []).map(id => byId.get(id))
      .filter(p => p && (!q || p.display_name.toLowerCase().includes(q)));
    if (!shown.length) {
      listEl.innerHTML = '<div class="nohits">Nothing matches that.</div>';
      return;
    }
    listEl.innerHTML = shown.map(p => {
      const bits = [];
      if (p.kind === "bill") {
        bits.push(fmtMoney(p.cost, DATA.currency));
      } else {
        bits.push(fmtInt(p.resource_count) + " resources");
        if (p.resource_count) {
          bits.push(p.all_unknown ? "usage unknown"
                                  : fmtInt(p.unused) + " unused / " + fmtInt(p.unknown) + " unknown");
        }
      }
      const bucket = ["shared", "ambiguous", "unassigned"].includes(p.kind);
      return '<button type="button" class="' + (selected === p.id ? "on" : "") +
        '" data-id="' + esc(p.id) + '">' +
        '<span class="dot" style="background:' + colourOf(p) + '"></span>' +
        '<span class="nm">' + esc(p.display_name) + "</span>" +
        (bucket ? ' <span class="sub">(' + esc(p.kind) + ")</span>" : "") +
        '<br><span class="sub">' + bits.map(esc).join(" &middot; ") + "</span></button>";
    }).join("");
    listEl.querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => select(b.dataset.id)));
  }

  // ---- selection and detail ----------------------------------------------

  function showDetail(id) {
    const p = id ? byId.get(id) : null;
    if (!p) {
      detailEl.innerHTML = '<span class="hint">Point at a ' + esc(view.noun) +
        " to see its numbers.</span>";
      return;
    }
    const rows = [];
    if (p.kind === "bill") {
      rows.push(["Billed", fmtMoney(p.cost, DATA.currency)]);
    } else {
      rows.push(["Resources", fmtInt(p.resource_count)],
        ["Potentially unused", p.resource_count ? fmtInt(p.unused) + " (" + fmtPct(p.unused_pct) + ")" : "-"],
        ["Active", fmtInt(p.active)],
        ["Usage unknown", fmtInt(p.unknown)]);
    }
    (p.type_counts || []).forEach(t => rows.push([t[0], fmtInt(t[1])]));
    if (p.grouping_method) rows.push(["Grouped by", p.grouping_method]);
    let html = '<div class="dt-title">' + esc(p.display_name) + "</div>";
    if (p.all_unknown) {
      html += '<div class="hint">No readable usage telemetry for anything here - ' +
              "0 unused means nothing could be measured.</div>";
    }
    if (p.why) html += '<div class="hint">' + esc(p.why) + "</div>";
    html += "<dl>" + rows.map(r => "<dt>" + esc(r[0]) + "</dt><dd>" + esc(String(r[1])) + "</dd>").join("") + "</dl>";
    detailEl.innerHTML = html;
  }

  function select(id) {
    selected = selected === id ? null : id;
    drawChart();
    drawList();
    showDetail(selected);
  }

  // ---- export -------------------------------------------------------------

  // The file has to explain itself away from this page, so the view, the
  // scale caveat and the scan date are drawn INTO the SVG.
  function exportSvg() {
    const svg = document.getElementById("bubblesSvg");
    if (!svg) return null;
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("viewBox", "0 0 " + S + " " + (S + 90));
    clone.setAttribute("width", S);
    clone.setAttribute("height", S + 90);
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bg.setAttribute("width", S); bg.setAttribute("height", S + 90); bg.setAttribute("fill", "#ffffff");
    clone.insertBefore(bg, clone.firstChild);
    const caption = [
      heading() + " - area is proportional to value",
      "Sizes are comparable within this view only. " + periodText(),
      "AWS scan " + DATA.scanned_at + (DATA.account ? " - account " + DATA.account : "")
    ];
    caption.forEach((line, i) => {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", 8);
      t.setAttribute("y", S + 26 + i * 24);
      t.setAttribute("font-size", 19);
      t.setAttribute("font-family", "Helvetica, Arial, sans-serif");
      t.setAttribute("fill", "#41505f");
      t.setAttribute("text-anchor", "start");
      t.textContent = line;
      clone.appendChild(t);
    });
    clone.querySelectorAll("text:not([font-family])").forEach(t => {
      t.setAttribute("font-family", "Helvetica, Arial, sans-serif");
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("fill", "#ffffff");
      t.setAttribute("font-weight", "600");
    });
    return new XMLSerializer().serializeToString(clone);
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  function fileStem() { return "aws-overview-" + view.id + "-" + metric; }

  document.getElementById("bubblesSvgBtn").addEventListener("click", () => {
    const text = exportSvg();
    if (text) download(new Blob([text], { type: "image/svg+xml" }), fileStem() + ".svg");
  });

  // Canvas rather than a library: SVG data URL -> <img> -> PNG.
  document.getElementById("bubblesPngBtn").addEventListener("click", () => {
    const text = exportSvg();
    if (!text) return;
    const img = new Image();
    img.onload = function () {
      const canvas = document.createElement("canvas");
      canvas.width = S * 2; canvas.height = (S + 90) * 2;   // 2x for a legible PNG
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(b => { if (b) download(b, fileStem() + ".png"); });
    };
    img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(text)));
  });

  // ---- controls -----------------------------------------------------------

  function drawControls() {
    viewsEl.innerHTML = DATA.views.map(v =>
      '<button type="button" data-view="' + esc(v.id) + '" class="' +
      (v.id === view.id ? "on" : "") + '">' + esc(v.label) + "</button>").join("");
    viewsEl.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      setView(b.dataset.view);
    }));
    metricsEl.innerHTML = view.metrics.map(m =>
      '<button type="button" data-metric="' + esc(m[0]) + '" class="' +
      (m[0] === metric ? "on" : "") + '"' + (metricAvailable(m[0]) ? "" : " disabled") +
      ' title="' + esc(m[2]) + '">' + esc(m[1]) + "</button>").join("");
    metricsEl.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
      metric = b.dataset.metric;
      selected = null;
      redraw();
    }));
    searchEl.placeholder = "Search " + view.noun + "s";
    searchEl.setAttribute("aria-label", searchEl.placeholder);
  }

  function drawNotes() {
    notesEl.innerHTML = view.notes.length
      ? "<ul>" + view.notes.map(n => "<li>" + esc(n) + "</li>").join("") + "</ul>"
      : "";
    notesEl.style.display = view.notes.length ? "" : "none";
  }

  function redraw() {
    drawControls(); drawChart(); drawScale(); drawList(); drawNotes();
    showDetail(selected);
  }

  // A metric this view does not offer, or has nothing to draw for, falls back
  // to the first one that does rather than opening on an empty explanation.
  function setView(id) {
    view = VIEWS.get(id) || view;
    byId = new Map(view.items.map(p => [p.id, p]));
    selected = null;
    if (!view.metrics.some(m => m[0] === metric) || !metricAvailable(metric)) {
      const fallback = view.metrics.map(m => m[0]).find(metricAvailable);
      metric = fallback || view.metrics[0][0];
    }
    redraw();
  }

  searchEl.addEventListener("input", drawList);
  setView(view.id);
})();
"""
