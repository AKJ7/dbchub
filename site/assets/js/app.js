/* dbchub frontend — browse/paging, detail view, and browser-side uploads (-> PR).
 *
 * Security notes:
 *  - Catalog data (which includes names parsed from contributed files) is treated as
 *    untrusted: it is only ever inserted with textContent / DOM APIs, never innerHTML,
 *    so markup in a file can't become live HTML.
 *  - Catalog JSON is fetched from this origin only (CSP connect-src 'self').
 *  - Upload inputs are validated before they are placed into GitHub API URLs.
 *  - The GitHub token is used only against api.github.com and never logged.
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var body = document.body.dataset;
  var CFG = {
    upstream: (body.upstream || "").trim(),
    baseBranch: (body.baseBranch || "master").trim(),
    browseJson: body.browseJson || "/browse.json",
    dbBase: body.dbBase || "/databases/",
    page: body.page || "other"
  };

  /* ---------------- helpers ---------------- */
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  function isHttps(u) { return typeof u === "string" && /^https:\/\//i.test(u); }
  function isSafeUrl(u) { return isHttps(u) || (typeof u === "string" && /^\/(?!\/)/.test(u)); }  // https or same-origin
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) {} }
  function fmtSize(n) {
    if (!(n >= 0)) return "—";
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function ts(iso) { var t = Date.parse(iso || ""); return isNaN(t) ? 0 : t; }
  // Same-origin URLs of a database file and its metadata JSON. Built from the
  // site's own /databases/ path rather than trusting URLs inside the catalog.
  function encPath(path) { return String(path).split("/").map(encodeURIComponent).join("/"); }
  function fileUrl(path) { return CFG.dbBase + encPath(path); }
  function metaUrl(path) { return CFG.dbBase + encPath(String(path).replace(/\.[^./]+$/, "") + ".json"); }

  // The slim catalog is fetched lazily and shared by the browse page and upload dialog.
  var catalogPromise = null;
  function loadCatalog() {
    if (!catalogPromise) {
      catalogPromise = fetch(CFG.browseJson, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (d) { return Array.isArray(d && d.databases) ? d.databases : []; })
        .catch(function () { catalogPromise = null; return []; });
    }
    return catalogPromise;
  }

  /* ---------------- modals ---------------- */
  var lastFocus = null;
  function openModal(id) {
    lastFocus = document.activeElement;
    var m = $("#" + id);
    m.classList.add("open");
    var f = $("input, select, button.close", m);
    if (f) f.focus();
  }
  function closeModal(bd) {
    bd.classList.remove("open");
    if (bd.id === "detail-modal" && /(^|[#&])db=/.test(location.hash)) {
      history.replaceState(null, "", location.pathname + location.search);
    }
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }
  $$(".modal-backdrop").forEach(function (bd) {
    bd.addEventListener("mousedown", function (e) { if (e.target === bd) closeModal(bd); });
    $$("[data-close]", bd).forEach(function (b) { b.addEventListener("click", function () { closeModal(bd); }); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $$(".modal-backdrop.open").forEach(closeModal);
  });

  /* ---------------- detail view ----------------
   * Overview comes from <name>.json; the Messages / Signals / Nodes tabs load
   * <name>.structure.json on first use. All values are untrusted → textContent only.
   * The open view is mirrored in the URL hash (#db=…&tab=…&msg=…|node=…) so it can
   * be shared and survives a reload. */
  var D = null;              // state of the open database
  var ROWS_STEP = 200;       // table rows rendered per "show more"
  var SAFE_PATH = /^(?!.*\.\.)[A-Za-z0-9._\/-]+$/;

  function addRow(dl, key, val) {
    dl.appendChild(el("dt", null, key));
    var dd = el("dd");
    if (val instanceof Node) dd.appendChild(val);
    else dd.textContent = (val == null || val === "") ? "—" : String(val);
    dl.appendChild(dd);
  }
  function setLink(a, url) {
    if (isSafeUrl(url)) { a.href = url; a.style.display = ""; }
    else { a.removeAttribute("href"); a.style.display = "none"; }
  }
  function structUrl(path) { return CFG.dbBase + encPath(String(path).replace(/\.[^./]+$/, "") + ".structure.json"); }
  function getJson(url) {
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function fmt(v) { return v == null || v === "" ? "—" : String(v); }
  function hexId(m) {
    if (m.id == null) return "—";
    var h = Number(m.id).toString(16).toUpperCase(), w = m.extended ? 8 : 3;
    while (h.length < w) h = "0" + h;
    return "0x" + h;
  }
  function linkBtn(text, onClick, cls) {
    var b = el("button", "linkbtn" + (cls ? " " + cls : ""), text);
    b.type = "button";
    b.addEventListener("click", function (e) { e.stopPropagation(); onClick(); });
    return b;
  }
  function nodeLinks(names) {
    var span = el("span", "nodelinks");
    if (!names || !names.length) { span.textContent = "—"; return span; }
    names.forEach(function (n, i) {
      if (i) span.appendChild(document.createTextNode(", "));
      span.appendChild(linkBtn(n, function () { go("nodes", { node: n }); }));
    });
    return span;
  }

  /* ---- tables ---- */
  // cols: [{label, cls}], rowFn(item) -> array of cell contents (string | Node);
  // onRow(item, tr) optional click handler. Renders ROWS_STEP rows at a time.
  function dataTable(items, cols, rowFn, onRow, emptyText) {
    var wrap = el("div", "tablewrap");
    if (!items.length) { wrap.appendChild(el("p", "muted", emptyText || "Nothing here.")); return wrap; }
    var table = el("table", "dtable"), thead = el("thead"), htr = el("tr");
    cols.forEach(function (c) { htr.appendChild(el("th", c.cls || null, c.label)); });
    thead.appendChild(htr); table.appendChild(thead);
    var tbody = el("tbody"); table.appendChild(tbody); wrap.appendChild(table);
    var shown = 0;
    var more = el("button", "btn small more", ""); more.type = "button";
    function renderMore() {
      var frag = document.createDocumentFragment();
      items.slice(shown, shown + ROWS_STEP).forEach(function (it) {
        var tr = el("tr");
        rowFn(it).forEach(function (cell, i) {
          var td = el("td", cols[i].cls || null);
          if (cell instanceof Node) td.appendChild(cell); else td.textContent = fmt(cell);
          tr.appendChild(td);
        });
        if (onRow) {
          tr.classList.add("clickable"); tr.tabIndex = 0;
          tr.addEventListener("click", function (e) { if (!e.target.closest("button, a")) onRow(it, tr); });
          tr.addEventListener("keydown", function (e) { if (e.key === "Enter" && e.target === tr) onRow(it, tr); });
        }
        frag.appendChild(tr);
      });
      tbody.appendChild(frag);
      shown = Math.min(items.length, shown + ROWS_STEP);
      more.hidden = shown >= items.length;
      more.textContent = "Show " + Math.min(ROWS_STEP, items.length - shown) + " more (" + (items.length - shown) + " left)";
    }
    more.addEventListener("click", renderMore);
    renderMore();
    wrap.appendChild(more);
    return wrap;
  }

  // A search box + results area; only the results re-render while typing.
  function searchable(panel, key, placeholder, renderResults) {
    var bar = el("div", "d-toolbar");
    var input = el("input", "d-search"); input.type = "search";
    input.placeholder = placeholder; input.value = D.q[key] || "";
    input.setAttribute("aria-label", placeholder);
    var count = el("span", "muted d-count");
    bar.appendChild(input); bar.appendChild(count);
    var results = el("div");
    panel.appendChild(bar); panel.appendChild(results);
    var t;
    function run() { results.textContent = ""; count.textContent = renderResults(results, (D.q[key] || "").toLowerCase().trim()); }
    input.addEventListener("input", function () {
      clearTimeout(t);
      t = setTimeout(function () { D.q[key] = input.value; run(); }, 100);
    });
    run();
    return input;
  }
  function matchesQ(q, parts) {
    if (!q) return true;
    var hay = parts.join(" ").toLowerCase();
    return q.split(/\s+/).every(function (term) { return hay.indexOf(term) !== -1; });
  }

  /* ---- signals ---- */
  var SIG_COLS = [
    { label: "Signal" }, { label: "Start", cls: "num" }, { label: "Len", cls: "num" }, { label: "Order" },
    { label: "Type" }, { label: "Factor", cls: "num" }, { label: "Offset", cls: "num" },
    { label: "Range", cls: "num" }, { label: "Unit" }, { label: "Receivers" }
  ];
  function sigType(s) { return s.float ? "float" : (s.signed ? "signed" : "unsigned"); }
  function sigCells(s) {
    var name = el("span", "signame", s.name);
    if (s.multiplex === "multiplexor") name.appendChild(el("span", "tag", "MUX"));
    else if (s.multiplex != null) name.appendChild(el("span", "tag", "m" + s.multiplex));
    if (s.values) name.appendChild(el("span", "tag soft", "enum"));
    return [name, s.start, s.length, s.byte_order === "little_endian" ? "Intel" : "Motorola", sigType(s),
      s.factor, s.offset, (s.min == null && s.max == null) ? null : fmt(s.min) + " … " + fmt(s.max),
      s.unit, nodeLinks(s.receivers)];
  }
  // Expand/collapse a details row under a signal row.
  function toggleSignalDetails(s, tr, msg) {
    var next = tr.nextElementSibling;
    if (next && next.classList.contains("sigdetail")) { next.remove(); tr.classList.remove("open"); return; }
    tr.classList.add("open");
    var dtr = el("tr", "sigdetail"), td = el("td");
    td.colSpan = tr.children.length;
    var dl = el("dl", "kv compact");
    if (msg) {
      addRow(dl, "Message", linkBtn(msg.name + " (" + hexId(msg) + ")", function () { go("messages", { msg: msg.name }); }));
    }
    addRow(dl, "Comment", s.comment);
    addRow(dl, "Physical value", "raw × " + fmt(s.factor) + " + " + fmt(s.offset) + (s.unit ? " [" + s.unit + "]" : ""));
    addRow(dl, "Bits", "start " + fmt(s.start) + ", length " + fmt(s.length) + ", " +
      (s.byte_order === "little_endian" ? "Intel (little-endian)" : "Motorola (big-endian)") + ", " + sigType(s));
    if (s.multiplex != null) addRow(dl, "Multiplexing", s.multiplex === "multiplexor" ? "multiplexor signal" : "present when multiplexor = " + s.multiplex);
    addRow(dl, "Receivers", nodeLinks(s.receivers));
    td.appendChild(dl);
    if (s.values) {
      var keys = Object.keys(s.values);
      var vt = el("div", "valtable");
      vt.appendChild(el("div", "vthead", "Value table (" + keys.length + ")"));
      var ul = el("ul");
      keys.sort(function (a, b) { return Number(a) - Number(b); }).forEach(function (k) {
        var li = el("li"); li.appendChild(el("code", null, k)); li.appendChild(document.createTextNode(" " + s.values[k]));
        ul.appendChild(li);
      });
      vt.appendChild(ul); td.appendChild(vt);
    }
    dtr.appendChild(td);
    tr.parentNode.insertBefore(dtr, tr.nextSibling);
  }

  /* ---- bit layout ---- */
  function sigBits(s) {
    var out = [], b = s.start, n = Math.min(s.length || 0, 512);
    if (b == null) return out;
    for (var i = 0; i < n; i++) {
      out.push(b);
      if (s.byte_order === "little_endian") b++;
      else b = (b % 8 === 0) ? b + 15 : b - 1;   // Motorola sawtooth: MSB → LSB
    }
    return out;
  }
  function bitLayout(m, sigs) {
    var bytes = m.length || 8;
    var box = el("div", "bitlayout");
    if (bytes > 64) { box.appendChild(el("p", "muted", "Bit layout is shown for frames up to 64 bytes.")); return box; }
    var cells = [];
    for (var i = 0; i < bytes * 8; i++) cells.push([]);
    sigs.forEach(function (s, idx) {
      sigBits(s).forEach(function (b, k) { if (b >= 0 && b < cells.length) cells[b].push({ idx: idx, msb: k === 0 }); });
    });
    var table = el("table", "bitgrid"), head = el("tr");
    head.appendChild(el("th", null, "Byte"));
    for (var bit = 7; bit >= 0; bit--) head.appendChild(el("th", null, String(bit)));
    var thead = el("thead"); thead.appendChild(head); table.appendChild(thead);
    var tbody = el("tbody");
    for (var by = 0; by < bytes; by++) {
      var tr = el("tr");
      tr.appendChild(el("th", null, String(by)));
      for (var bt = 7; bt >= 0; bt--) {
        var n = by * 8 + bt, occ = cells[n], td = el("td");
        if (occ.length) {
          td.className = occ.length > 1 ? "overlap" : "c" + (occ[0].idx % 12);
          td.dataset.sigs = occ.map(function (o) { return o.idx; }).join(" ");
          td.title = "bit " + n + ": " + occ.map(function (o) { return sigs[o.idx].name; }).join(", ");
          if (occ.some(function (o) { return o.msb; })) td.classList.add("msb");
        }
        td.appendChild(el("span", "bitno", String(n)));
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    box.appendChild(table);

    var legend = el("ul", "legend");
    sigs.forEach(function (s, idx) {
      var li = el("li");
      li.appendChild(el("span", "sw c" + (idx % 12)));
      li.appendChild(document.createTextNode(s.name));
      li.addEventListener("mouseenter", function () { highlight(table, idx); });
      li.addEventListener("mouseleave", function () { highlight(table, null); });
      legend.appendChild(li);
    });
    box.appendChild(legend);
    box.appendChild(el("p", "muted small", "Hover a signal to highlight its bits. ▸ marks the start bit; hatched cells are shared by several (e.g. multiplexed) signals."));
    return box;
  }
  function highlight(table, idx) {
    $$("td[data-sigs]", table).forEach(function (td) {
      var on = idx != null && td.dataset.sigs.split(" ").indexOf(String(idx)) !== -1;
      td.classList.toggle("hl", on);
      td.classList.toggle("dim", idx != null && !on);
    });
  }

  /* ---- tab renderers ---- */
  function renderMessages(panel) {
    var S = D.struct;
    if (D.view.msg != null) {
      var m = S.messages.filter(function (x) { return x.name === D.view.msg; })[0];
      if (m) return renderMessage(panel, m);
    }
    searchable(panel, "messages", "Filter by ID, name, sender, signal…", function (out, q) {
      var rows = S.messages.filter(function (m) {
        return matchesQ(q, [hexId(m), String(m.id), m.name, m.comment || "", (m.senders || []).join(" "),
          m.signals.map(function (s) { return s.name; }).join(" ")]);
      });
      out.appendChild(dataTable(rows,
        [{ label: "ID", cls: "mono" }, { label: "Name" }, { label: "Bytes", cls: "num" }, { label: "Senders" },
         { label: "Cycle", cls: "num" }, { label: "Signals", cls: "num" }],
        function (m) {
          return [hexId(m) + (m.extended ? " ext" : ""), m.name, m.length, nodeLinks(m.senders),
            m.cycle_ms ? m.cycle_ms + " ms" : null, m.signals.length];
        },
        function (m) { go("messages", { msg: m.name }); },
        "No messages match."));
      return rows.length + " of " + S.messages.length;
    });
  }

  function renderMessage(panel, m) {
    panel.appendChild(linkBtn("← All messages", function () { go("messages", {}); }, "back"));
    var h = el("h3", "d-sectiontitle", m.name);
    h.appendChild(el("span", "mono muted", " " + hexId(m)));
    panel.appendChild(h);
    var dl = el("dl", "kv compact");
    addRow(dl, "Identifier", hexId(m) + " (" + fmt(m.id) + ") · " + (m.extended ? "extended 29-bit" : "standard 11-bit") + (m.fd ? " · CAN FD" : ""));
    addRow(dl, "Length", fmt(m.length) + " bytes");
    addRow(dl, "Senders", nodeLinks(m.senders));
    addRow(dl, "Cycle time", m.cycle_ms ? m.cycle_ms + " ms" : null);
    if (m.bus) addRow(dl, "Bus", m.bus);
    addRow(dl, "Comment", m.comment);
    panel.appendChild(dl);

    var muxVals = [];
    m.signals.forEach(function (s) {
      if (typeof s.multiplex === "number" && muxVals.indexOf(s.multiplex) === -1) muxVals.push(s.multiplex);
    });
    muxVals.sort(function (a, b) { return a - b; });
    var layoutBox = el("div"), sigBox = el("div");
    function draw() {
      var mv = D.view.mux;
      var sigs = m.signals.filter(function (s) {
        return mv == null || mv === "all" || typeof s.multiplex !== "number" || String(s.multiplex) === String(mv);
      });
      layoutBox.textContent = ""; sigBox.textContent = "";
      layoutBox.appendChild(bitLayout(m, sigs));
      sigBox.appendChild(dataTable(sigs, SIG_COLS, sigCells, function (s, tr) { toggleSignalDetails(s, tr, null); }, "This message has no signals."));
    }
    panel.appendChild(el("h4", "d-subhead", "Bit layout"));
    if (muxVals.length) {
      var lab = el("label", "muxsel", "Multiplexor value ");
      var sel = el("select");
      [["all", "all (overlapping)"]].concat(muxVals.map(function (v) { return [String(v), String(v)]; })).forEach(function (o) {
        var opt = el("option", null, o[1]); opt.value = o[0]; sel.appendChild(opt);
      });
      sel.value = D.view.mux != null ? String(D.view.mux) : "all";
      sel.addEventListener("change", function () { D.view.mux = sel.value; draw(); });
      lab.appendChild(sel);
      panel.appendChild(lab);
    }
    panel.appendChild(layoutBox);
    panel.appendChild(el("h4", "d-subhead", "Signals (" + m.signals.length + ")"));
    panel.appendChild(el("p", "muted small", "Click a signal for its comment, scaling and value table."));
    panel.appendChild(sigBox);
    draw();
  }

  function renderSignals(panel) {
    var all = D.allSignals;
    var cols = [{ label: "Message" }].concat(SIG_COLS);
    searchable(panel, "signals", "Filter by signal, message, unit, receiver, comment…", function (out, q) {
      var rows = all.filter(function (x) {
        var s = x.s;
        return matchesQ(q, [s.name, x.m.name, hexId(x.m), s.unit || "", (s.receivers || []).join(" "), s.comment || ""]);
      });
      out.appendChild(dataTable(rows, cols,
        function (x) {
          return [linkBtn(x.m.name, function () { go("messages", { msg: x.m.name }); })].concat(sigCells(x.s));
        },
        function (x, tr) { toggleSignalDetails(x.s, tr, x.m); },
        "No signals match."));
      return rows.length + " of " + all.length;
    });
  }

  function renderNodes(panel) {
    var S = D.struct;
    if (D.view.node != null) {
      var n = S.nodes.filter(function (x) { return x.name === D.view.node; })[0];
      if (n) return renderNode(panel, n);
    }
    searchable(panel, "nodes", "Filter nodes…", function (out, q) {
      var rows = S.nodes.filter(function (n) { return matchesQ(q, [n.name, n.comment || ""]); });
      out.appendChild(dataTable(rows,
        [{ label: "Node" }, { label: "Sends (messages)", cls: "num" }, { label: "Receives (signals)", cls: "num" }, { label: "Comment" }],
        function (n) { var u = D.usage[n.name] || { tx: [], rx: [] }; return [n.name, u.tx.length, u.rx.length, n.comment]; },
        function (n) { go("nodes", { node: n.name }); },
        "No nodes match."));
      return rows.length + " of " + S.nodes.length;
    });
  }

  function renderNode(panel, n) {
    var u = D.usage[n.name] || { tx: [], rx: [] };
    panel.appendChild(linkBtn("← All nodes", function () { go("nodes", {}); }, "back"));
    panel.appendChild(el("h3", "d-sectiontitle", n.name));
    if (n.comment) panel.appendChild(el("p", null, n.comment));
    panel.appendChild(el("h4", "d-subhead", "Sends " + u.tx.length + " message" + (u.tx.length === 1 ? "" : "s")));
    panel.appendChild(dataTable(u.tx,
      [{ label: "ID", cls: "mono" }, { label: "Name" }, { label: "Bytes", cls: "num" }, { label: "Cycle", cls: "num" }, { label: "Signals", cls: "num" }],
      function (m) { return [hexId(m), m.name, m.length, m.cycle_ms ? m.cycle_ms + " ms" : null, m.signals.length]; },
      function (m) { go("messages", { msg: m.name }); }, "This node sends no messages."));
    panel.appendChild(el("h4", "d-subhead", "Receives " + u.rx.length + " signal" + (u.rx.length === 1 ? "" : "s")));
    panel.appendChild(dataTable(u.rx,
      [{ label: "Signal" }, { label: "Message" }, { label: "Unit" }],
      function (x) { return [x.s.name, linkBtn(x.m.name, function () { go("messages", { msg: x.m.name }); }), x.s.unit]; },
      function (x, tr) { toggleSignalDetails(x.s, tr, x.m); }, "This node receives no signals."));
  }

  /* ---- loading + navigation ---- */
  function loadStruct() {
    if (!D.structPromise) {
      var path = D.path;
      D.structPromise = getJson(structUrl(path)).then(function (S) {
        if (!D || D.path !== path) return;
        S.messages = Array.isArray(S.messages) ? S.messages : [];
        S.nodes = Array.isArray(S.nodes) ? S.nodes : [];
        var all = [], usage = {};
        S.nodes.forEach(function (n) { usage[n.name] = { tx: [], rx: [] }; });
        function use(n) { return usage[n] || (usage[n] = { tx: [], rx: [] }); }
        S.messages.forEach(function (m) {
          m.signals = Array.isArray(m.signals) ? m.signals : [];
          (m.senders || []).forEach(function (n) { use(n).tx.push(m); });
          m.signals.forEach(function (s) {
            var x = { s: s, m: m };
            all.push(x);
            (s.receivers || []).forEach(function (n) { use(n).rx.push(x); });
          });
        });
        D.struct = S; D.allSignals = all; D.usage = usage;
        $("#d-n-messages").textContent = S.messages.length;
        $("#d-n-signals").textContent = all.length;
        $("#d-n-nodes").textContent = S.nodes.length;
      });
    }
    return D.structPromise;
  }

  function go(tab, view) {
    if (!D) return;
    D.tab = tab;
    D.view = view || {};
    $$("#d-tabs [data-tab]").forEach(function (b) {
      var on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.classList.toggle("active", on);
    });
    $$("#detail-modal .tabpanel").forEach(function (p) { p.hidden = p.dataset.panel !== tab; });
    writeHash();
    var panel = $('#detail-modal .tabpanel[data-panel="' + tab + '"]');
    if (tab === "overview" || tab === "lint") return;
    panel.textContent = "";
    panel.appendChild(el("p", "muted", "Loading structure…"));
    var path = D.path;
    loadStruct().then(function () {
      if (!D || D.path !== path || D.tab !== tab) return;
      panel.textContent = "";
      if (tab === "messages") renderMessages(panel);
      else if (tab === "signals") renderSignals(panel);
      else if (tab === "nodes") renderNodes(panel);
      $(".d-body").scrollTop = 0;
    }).catch(function () {
      panel.textContent = "";
      panel.appendChild(el("p", "muted", "The structure of this database is not available (it may not have parsed)."));
    });
  }

  function writeHash() {
    var p = new URLSearchParams();
    p.set("db", D.path);
    if (D.tab !== "overview") p.set("tab", D.tab);
    if (D.view.msg != null) p.set("msg", D.view.msg);
    if (D.view.node != null) p.set("node", D.view.node);
    history.replaceState(null, "", location.pathname + location.search + "#" + p.toString());
  }

  function showDetail(path, opts) {
    if (!SAFE_PATH.test(String(path || ""))) return;
    opts = opts || {};
    D = { path: path, tab: "overview", view: {}, q: { messages: "", signals: "", nodes: "" }, struct: null, structPromise: null };
    $("#d-title").textContent = String(path).split("/").pop();
    $("#d-sub").textContent = String(path).split("/").slice(0, 2).join(" / ");
    $("#d-badges").textContent = "";
    ["messages", "signals", "nodes", "lint"].forEach(function (k) { $("#d-n-" + k).textContent = ""; });
    var dl = $("#d-kv");
    dl.textContent = "";
    dl.appendChild(el("dd", "loading", "Loading…"));
    $("#d-warn").textContent = "";
    setLink($("#d-download"), fileUrl(path));
    setLink($("#d-meta"), null);
    setLink($("#d-struct"), null);
    openModal("detail-modal");
    var tab = ["overview", "messages", "signals", "nodes", "lint"].indexOf(opts.tab) !== -1 ? opts.tab : "overview";
    go(tab, { msg: opts.msg, node: opts.node });

    var url = metaUrl(path);
    getJson(url).then(function (d) {
      if (!D || D.path !== path) return;
      renderOverview(d, url);
    }).catch(function () {
      dl.textContent = "";
      dl.appendChild(el("dd", "loading", "Could not load the metadata for this database."));
    });
  }

  function renderOverview(d, url) {
    $("#d-title").textContent = d.name;
    $("#d-sub").textContent = d.manufacturer + " / " + d.model;
    var badges = $("#d-badges");
    badges.textContent = "";
    badges.appendChild(el("span", "badge " + String(d.format || "").replace(/[^a-z0-9]/gi, ""), d.format));
    badges.appendChild(d.valid ? el("span", "pill ok", "● valid") : el("span", "pill bad", "● invalid"));
    badges.appendChild(el("span", "ns", String(d.bus || "").toUpperCase()));
    badges.appendChild(el("span", "ns", d.license || ""));

    var c = d.counts || {};
    if (!D.struct) {
      $("#d-n-messages").textContent = fmt(c.messages);
      $("#d-n-signals").textContent = fmt(c.signals);
      $("#d-n-nodes").textContent = fmt(c.nodes);
    }
    var dl = $("#d-kv");
    dl.textContent = "";
    addRow(dl, "Contents", (c.messages || 0) + " messages · " + (c.signals || 0) + " signals · " + (c.nodes || 0) + " nodes");
    addRow(dl, "Format", (d.format || "").toUpperCase());
    addRow(dl, "Bus", (d.bus || "").toUpperCase());
    addRow(dl, "Size", fmtSize(d.size_bytes));
    addRow(dl, "License", d.license || "CC-BY-4.0");
    if (d.source) {
      var src = el("span");
      if (isHttps(d.source_url)) {
        var sa = el("a", null, d.source);
        sa.href = d.source_url; sa.target = "_blank"; sa.rel = "noopener noreferrer";
        src.appendChild(sa);
      } else { src.textContent = d.source; }
      if (d.attribution) src.appendChild(document.createTextNode(" — " + d.attribution));
      addRow(dl, "Source", src);
    }
    addRow(dl, "Added", d.added);
    addRow(dl, "Updated", d.updated);
    addRow(dl, "Commit", d.commit);
    addRow(dl, "Checksum", d.checksum);
    addRow(dl, "Path", d.path);

    var warns = (d.errors || []).map(function (w) { return "error: " + w; }).concat(d.warnings || []);
    var warn = $("#d-warn");
    warn.textContent = "";
    warns.slice(0, 500).forEach(function (w) { warn.appendChild(el("li", null, w)); });
    if (warns.length > 500) warn.appendChild(el("li", null, "… and " + (warns.length - 500) + " more (see metadata JSON)"));
    $("#d-lint-empty").hidden = !!warns.length;
    $("#d-n-lint").textContent = warns.length;

    setLink($("#d-meta"), url);
    setLink($("#d-struct"), d.structure_url === null ? null : structUrl(d.path));
  }

  $$("#d-tabs [data-tab]").forEach(function (b) {
    b.addEventListener("click", function () { go(b.dataset.tab, {}); });
  });
  // Left/right arrow keys move between tabs.
  $("#d-tabs").addEventListener("keydown", function (e) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    var tabs = $$("#d-tabs [data-tab]"), i = tabs.indexOf(document.activeElement);
    if (i === -1) return;
    var next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    next.focus(); next.click();
  });
  $("#d-copy").addEventListener("click", function () {
    var btn = this, done = function (ok) {
      btn.textContent = ok ? "✓ Copied" : "Copy failed";
      setTimeout(function () { btn.textContent = "🔗 Copy link"; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(location.href).then(function () { done(true); }, function () { done(false); });
    } else { done(false); }
  });

  function openDetailFromHash() {
    var h = new URLSearchParams(location.hash.replace(/^#/, ""));
    if (h.get("db")) showDetail(h.get("db"), { tab: h.get("tab"), msg: h.get("msg"), node: h.get("node") });
  }
  // A pasted/edited #db=… link on an already-open page (our own replaceState
  // calls don't fire hashchange, so this only reacts to the user).
  window.addEventListener("hashchange", function () {
    if (/(^|[#&])db=/.test(location.hash)) openDetailFromHash();
    else if (D) $$("#detail-modal.open").forEach(closeModal);
  });

  // Event delegation: works for server-rendered (home) and JS-rendered (browse) cards.
  document.addEventListener("click", function (e) {
    var card = e.target.closest && e.target.closest(".card");
    if (!card || e.target.closest("a")) return;
    showDetail(card.dataset.path);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || !e.target.classList || !e.target.classList.contains("card")) return;
    showDetail(e.target.dataset.path);
  });

  /* ---------------- card (mirror of _includes/card.html) ---------------- */
  function cardEl(d, when) {
    var c = d.counts || {};
    var card = el("article", "card");
    card.dataset.path = d.path;
    card.tabIndex = 0;
    card.appendChild(el("div", "ns", d.manufacturer + " / " + d.model));
    var title = el("div", "title");
    title.appendChild(el("span", "name", d.name));
    title.appendChild(el("span", "badge " + String(d.format || "").replace(/[^a-z0-9]/gi, ""), d.format));
    card.appendChild(title);
    var row = el("div", "row");
    row.appendChild(d.valid ? el("span", "pill ok", "● valid") : el("span", "pill bad", "● invalid"));
    row.appendChild(el("span", "ns", String(d.bus || "").toUpperCase()));
    row.appendChild(el("span", "ns", d.license || ""));
    card.appendChild(row);
    var stats = el("div", "stats");
    [["messages", c.messages], ["signals", c.signals], ["nodes", c.nodes]].forEach(function (p) {
      var s = el("span"); s.appendChild(el("b", null, p[1] == null ? "—" : p[1]));
      s.appendChild(document.createTextNode(" " + p[0])); stats.appendChild(s);
    });
    card.appendChild(stats);
    var foot = el("div", "foot");
    foot.appendChild(el("span", "when", when === "added" ? "Added " + (d.added || "—") : "Updated " + (d.updated || "—")));
    var actions = el("span", "actions");
    var a = el("a", "btn small primary", "↓ Download");
    a.href = fileUrl(d.path); a.setAttribute("download", "");
    actions.appendChild(a);
    actions.appendChild(el("button", "btn small js-detail", "Details")).type = "button";
    foot.appendChild(actions);
    card.appendChild(foot);
    return card;
  }

  /* ---------------- browse page ---------------- */
  function initBrowse() {
    var search = $("#search");
    var grid = $("#b-grid"), empty = $("#b-empty"), pager = $("#b-pager"), count = $("#b-count");
    var sortSel = $("#b-sort"), perSel = $("#b-per"), clearBtn = $("#clear-filters");
    var FACETS = [
      { key: "mfr", field: "manufacturer", box: "#facet-mfr", label: function (v) { return v; } },
      { key: "fmt", field: "format", box: "#facet-fmt", label: function (v) { return v.toUpperCase(); } },
      { key: "bus", field: "bus", box: "#facet-bus", label: function (v) { return v.toUpperCase(); } },
      { key: "lic", field: "license", box: "#facet-lic", label: function (v) { return v; } }
    ];
    var SORTS = {
      manufacturer: function (a, b) { return cmp(a.manufacturer, b.manufacturer) || cmp(a.model, b.model) || cmp(a.name, b.name); },
      name: function (a, b) { return cmp(a.name, b.name) || cmp(a.path, b.path); },
      updated: function (a, b) { return ts(b.updated_at) - ts(a.updated_at) || cmp(a.path, b.path); },
      added: function (a, b) { return ts(b.added_at) - ts(a.added_at) || cmp(a.path, b.path); },
      signals: function (a, b) { return ((b.counts || {}).signals || 0) - ((a.counts || {}).signals || 0) || cmp(a.path, b.path); }
    };
    function cmp(a, b) { return String(a || "").localeCompare(String(b || ""), undefined, { sensitivity: "base", numeric: true }); }

    var ALL = [];
    var state = readState();

    function readState() {
      var p = new URLSearchParams(location.search);
      var s = {
        q: p.get("q") || "",
        sort: SORTS[p.get("sort")] ? p.get("sort") : "manufacturer",
        per: [12, 24, 48, 96].indexOf(+p.get("per")) !== -1 ? +p.get("per") : 24,
        page: Math.max(1, parseInt(p.get("page"), 10) || 1)
      };
      FACETS.forEach(function (f) {
        s[f.key] = (p.get(f.key) || "").split(",").filter(Boolean).map(function (v) { return v.toLowerCase(); });
      });
      return s;
    }
    function writeState(push) {
      var p = new URLSearchParams();
      if (state.q) p.set("q", state.q);
      FACETS.forEach(function (f) { if (state[f.key].length) p.set(f.key, state[f.key].join(",")); });
      if (state.sort !== "manufacturer") p.set("sort", state.sort);
      if (state.per !== 24) p.set("per", state.per);
      if (state.page > 1) p.set("page", state.page);
      var qs = p.toString();
      var url = location.pathname + (qs ? "?" + qs : "") + location.hash;
      if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
    }

    function matches(d, skipKey) {
      if (state.q) {
        var hay = (d.name + " " + d.manufacturer + " " + d.model + " " + d.bus + " " + d.format + " " + d.path).toLowerCase();
        var terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
        for (var i = 0; i < terms.length; i++) if (hay.indexOf(terms[i]) === -1) return false;
      }
      for (var j = 0; j < FACETS.length; j++) {
        var f = FACETS[j], sel = state[f.key];
        if (f.key === skipKey || !sel.length) continue;
        if (sel.indexOf(String(d[f.field] || "").toLowerCase()) === -1) return false;
      }
      return true;
    }

    // Facet counts reflect the other active filters (standard faceted-search behaviour).
    function renderFacets() {
      FACETS.forEach(function (f) {
        var counts = {}, shown = {};
        ALL.forEach(function (d) {
          var v = String(d[f.field] || "").toLowerCase();
          if (!(v in counts)) { counts[v] = 0; shown[v] = String(d[f.field] || ""); }
          if (matches(d, f.key)) counts[v]++;
        });
        var box = $(f.box);
        box.textContent = "";
        Object.keys(counts).sort(cmp).forEach(function (v) {
          var lab = el("label", "facet");
          var cb = el("input"); cb.type = "checkbox"; cb.value = v;
          cb.checked = state[f.key].indexOf(v) !== -1;
          cb.addEventListener("change", function () {
            var s = state[f.key];
            if (cb.checked) s.push(v); else s.splice(s.indexOf(v), 1);
            state.page = 1; update(false);
          });
          lab.appendChild(cb);
          lab.appendChild(el("span", "label", f.label(shown[v])));
          lab.appendChild(el("span", "n", counts[v]));
          if (!counts[v] && !cb.checked) lab.classList.add("zero");
          lab.dataset.v = v;
          box.appendChild(lab);
        });
      });
      filterMfrList();
      var any = !!state.q || FACETS.some(function (f) { return state[f.key].length; });
      clearBtn.hidden = !any;
    }
    function filterMfrList() {
      var q = ($("#mfr-filter").value || "").trim().toLowerCase();
      $$("#facet-mfr .facet").forEach(function (l) { l.hidden = !!q && l.dataset.v.indexOf(q) === -1; });
    }

    function pageButton(label, page, opts) {
      var b = el("button", "pg" + (opts && opts.current ? " current" : ""), label);
      b.type = "button";
      if (opts && opts.aria) b.setAttribute("aria-label", opts.aria);
      if (opts && opts.current) b.setAttribute("aria-current", "page");
      if (page == null) b.disabled = true;
      else b.addEventListener("click", function () {
        state.page = page; update(true);
        grid.scrollIntoView({ block: "start", behavior: "smooth" });
      });
      return b;
    }
    function renderPager(pages) {
      pager.textContent = "";
      if (pages <= 1) return;
      var p = state.page;
      pager.appendChild(pageButton("‹ Prev", p > 1 ? p - 1 : null, { aria: "Previous page" }));
      var nums = [1, p - 1, p, p + 1, pages].filter(function (n, i, a) { return n >= 1 && n <= pages && a.indexOf(n) === i; })
        .sort(function (a, b) { return a - b; });
      var prev = 0;
      nums.forEach(function (n) {
        if (n - prev > 1) pager.appendChild(el("span", "gap", "…"));
        pager.appendChild(pageButton(String(n), n === p ? null : n, { current: n === p, aria: "Page " + n }));
        prev = n;
      });
      pager.appendChild(pageButton("Next ›", p < pages ? p + 1 : null, { aria: "Next page" }));
    }

    function update(push) {
      var rows = ALL.filter(function (d) { return matches(d); }).sort(SORTS[state.sort]);
      var pages = Math.max(1, Math.ceil(rows.length / state.per));
      if (state.page > pages) state.page = pages;
      var start = (state.page - 1) * state.per;
      var slice = rows.slice(start, start + state.per);
      var when = state.sort === "added" ? "added" : "updated";

      var frag = document.createDocumentFragment();
      slice.forEach(function (d) { frag.appendChild(cardEl(d, when)); });
      grid.textContent = "";
      grid.appendChild(frag);
      grid.hidden = !rows.length;
      empty.hidden = !!rows.length;
      count.textContent = rows.length
        ? "Showing " + (start + 1) + "–" + (start + slice.length) + " of " + rows.length +
          (rows.length !== ALL.length ? " (filtered from " + ALL.length + ")" : "")
        : "0 of " + ALL.length;
      renderPager(pages);
      renderFacets();
      writeState(push);
    }

    function syncControls() {
      search.value = state.q;
      sortSel.value = state.sort;
      perSel.value = String(state.per);
    }

    var debounce;
    search.addEventListener("input", function () {
      clearTimeout(debounce);
      debounce = setTimeout(function () { state.q = search.value.trim(); state.page = 1; update(false); }, 120);
    });
    search.form.addEventListener("submit", function (e) { e.preventDefault(); });
    sortSel.addEventListener("change", function () { state.sort = sortSel.value; state.page = 1; update(false); });
    perSel.addEventListener("change", function () { state.per = +perSel.value; state.page = 1; update(false); });
    $("#mfr-filter").addEventListener("input", filterMfrList);
    clearBtn.addEventListener("click", function () {
      state.q = ""; FACETS.forEach(function (f) { state[f.key] = []; });
      state.page = 1; syncControls(); update(false);
    });
    window.addEventListener("popstate", function () { state = readState(); syncControls(); update(false); });

    syncControls();
    loadCatalog().then(function (rows) {
      ALL = rows;
      if (!rows.length) { count.textContent = "Could not load the catalog."; return; }
      update(false);
    });
  }

  /* ---------------- upload -> pull request ---------------- */
  function initUpload() {
    var TOKEN_KEY = "dbchub.token", FORK_KEY = "dbchub.fork";
    var MAX_BYTES = 25 * 1024 * 1024;
    var SLUG = /^[a-z0-9][a-z0-9._-]*$/;              // manufacturer / model / bus
    var REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;  // owner/repo
    var FILE = /^[A-Za-z0-9][A-Za-z0-9._-]*\.(dbc|arxml|ldf|dbf|kcd|sym)$/i;
    // Default bus suggested from the file type (only when the user hasn't chosen one).
    var EXT_BUS = { ldf: "lin" };

    var fileIn = $("#u-file"), drop = $("#u-drop"), info = $("#u-file-info");
    var mfrIn = $("#u-mfr"), modelIn = $("#u-model"), busSel = $("#u-bus"), busOther = $("#u-bus-other");
    var forkIn = $("#u-fork"), tokenIn = $("#u-token"), remember = $("#u-remember");
    var pathEl = $("#u-path"), steps = $("#u-steps"), submit = $("#u-submit");
    var busTouched = false, existing = {}, models = {};

    var savedTok = lsGet(TOKEN_KEY), savedFork = lsGet(FORK_KEY);
    if (savedTok) tokenIn.value = savedTok;
    if (savedFork) forkIn.value = savedFork;
    remember.checked = !!(savedTok || savedFork);

    function slugify(v) {
      return String(v || "").trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9._-]/g, "").replace(/^[._-]+/, "");
    }
    function bus() { return busSel.value === "__other" ? slugify(busOther.value) : busSel.value; }
    function chosenFile() { return fileIn.files && fileIn.files[0]; }
    function fileName() { var f = chosenFile(); return f ? f.name.split(/[\\/]/).pop() : ""; }

    // Suggestions from the existing catalog: manufacturers, then models for the chosen one.
    function fillDatalist(id, values) {
      var dl = $(id); dl.textContent = "";
      values.forEach(function (v) { var o = el("option"); o.value = v; dl.appendChild(o); });
    }
    function prepareSuggestions() {
      loadCatalog().then(function (rows) {
        existing = {}; models = {};
        rows.forEach(function (d) {
          existing[d.path] = true;
          (models[d.manufacturer] = models[d.manufacturer] || {})[d.model] = true;
        });
        fillDatalist("#u-mfr-list", Object.keys(models).sort());
        fillModels();
        preview();
      });
    }
    function fillModels() {
      fillDatalist("#u-model-list", Object.keys(models[slugify(mfrIn.value)] || {}).sort());
    }

    function showFile() {
      var f = chosenFile();
      info.textContent = "";
      drop.classList.toggle("has-file", !!f);
      info.hidden = !f;
      if (!f) return;
      info.appendChild(el("b", null, fileName()));
      info.appendChild(document.createTextNode(" · " + fmtSize(f.size)));
      var ext = (fileName().split(".").pop() || "").toLowerCase();
      if (!busTouched && EXT_BUS[ext]) busSel.value = EXT_BUS[ext];
      if (f.size > MAX_BYTES) info.appendChild(el("span", "bad", " · too large (max 25 MB)"));
      else if (!FILE.test(fileName())) info.appendChild(el("span", "bad", " · unsupported name or extension"));
    }

    function preview() {
      var parts = [slugify(mfrIn.value) || "‹manufacturer›", slugify(modelIn.value) || "‹model›", bus() || "‹bus›", fileName() || "‹file›"];
      var path = parts.join("/");
      pathEl.textContent = "";
      pathEl.appendChild(document.createTextNode("Will be added as "));
      pathEl.appendChild(el("code", null, "databases/" + path));
      if (existing[path]) {
        pathEl.appendChild(el("span", "warn", " — a database already exists here; your pull request will update it."));
      }
    }

    // Normalise slug fields on blur so what the user sees is what gets committed.
    [mfrIn, modelIn, busOther].forEach(function (inp) {
      inp.addEventListener("blur", function () { inp.value = slugify(inp.value); preview(); });
      inp.addEventListener("input", preview);
    });
    mfrIn.addEventListener("input", fillModels);
    mfrIn.addEventListener("change", fillModels);
    busSel.addEventListener("change", function () {
      busTouched = true;
      busOther.hidden = busSel.value !== "__other";
      if (!busOther.hidden) busOther.focus();
      preview();
    });
    fileIn.addEventListener("change", function () { showFile(); preview(); });

    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        fileIn.files = e.dataTransfer.files;
        showFile(); preview();
      }
    });

    $("#open-upload").addEventListener("click", function () {
      prepareSuggestions();
      preview();
      openModal("upload-modal");
    });

    /* ---- GitHub API ---- */
    function gh(method, path, token, payload) {
      return fetch("https://api.github.com" + path, {
        method: method,
        headers: {
          Authorization: "Bearer " + token,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28"
        },
        body: payload ? JSON.stringify(payload) : undefined
      }).then(function (r) {
        return r.text().then(function (t) {
          var data = {};
          try { data = t ? JSON.parse(t) : {}; } catch (e) { data = {}; }
          return { status: r.status, data: data };
        });
      });
    }
    function toBase64(buf) {
      var bytes = new Uint8Array(buf), bin = "", chunk = 0x8000;
      for (var i = 0; i < bytes.length; i += chunk) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
      return btoa(bin);
    }
    function readFile(file) {
      return new Promise(function (res, rej) {
        var fr = new FileReader();
        fr.onload = function () { res(toBase64(fr.result)); };
        fr.onerror = function () { rej(new Error("Could not read the file.")); };
        fr.readAsArrayBuffer(file);
      });
    }
    function msg(kind, text) {
      var r = $("#u-result");
      r.className = "result show " + kind;
      r.textContent = text;
      return r;
    }
    function invalid(input, text) {
      if (input) { input.classList.add("invalid"); input.focus(); }
      msg("err", text);
    }

    var STEP_NAMES = ["Read file", "Create branch on your fork", "Commit file", "Open pull request"];
    function setStep(i, status) {
      var li = steps.children[i];
      li.className = status;
    }
    function resetSteps() {
      steps.textContent = "";
      STEP_NAMES.forEach(function (n) { steps.appendChild(el("li", "todo", n)); });
      steps.hidden = false;
    }

    $("#upload-form").addEventListener("input", function (e) {
      if (e.target.classList) e.target.classList.remove("invalid");
    });

    $("#upload-form").addEventListener("submit", function (e) {
      e.preventDefault();
      $$(".invalid", e.currentTarget).forEach(function (x) { x.classList.remove("invalid"); });
      var file = chosenFile();
      var filename = fileName();
      mfrIn.value = slugify(mfrIn.value);
      modelIn.value = slugify(modelIn.value);
      var mfr = mfrIn.value, model = modelIn.value, b = bus();
      var fork = forkIn.value.trim(), token = tokenIn.value.trim();

      // ---- validation (also stops path traversal / URL injection) ----
      if (!file) return invalid(drop, "Choose a file to upload.");
      if (!FILE.test(filename)) return invalid(drop, "File must be .dbc/.arxml/.ldf/.dbf/.kcd/.sym with a plain name (letters, digits, . _ -).");
      if (file.size > MAX_BYTES) return invalid(drop, "File is larger than 25 MB.");
      if (!SLUG.test(mfr)) return invalid(mfrIn, "Manufacturer: use lowercase letters, digits, dot, dash, underscore.");
      if (!SLUG.test(model)) return invalid(modelIn, "Model: use lowercase letters, digits, dot, dash, underscore.");
      if (!SLUG.test(b)) return invalid(busSel.value === "__other" ? busOther : busSel, "Bus: pick one from the list or enter a lowercase slug.");
      if (!REPO.test(fork)) return invalid(forkIn, "Fork must look like owner/repo.");
      if (!token) return invalid(tokenIn, "A GitHub token is required.");
      if (!$("#u-rights").checked) return invalid($("#u-rights"), "Please confirm you have the right to publish this file.");
      if (CFG.upstream && !REPO.test(CFG.upstream)) return msg("err", "Site is misconfigured: upstream_repo is invalid.");

      lsSet(TOKEN_KEY, remember.checked ? token : null);
      lsSet(FORK_KEY, remember.checked ? fork : null);

      var upstream = CFG.upstream || fork;
      var base = CFG.baseBranch || "master";
      var repoPath = "databases/" + mfr + "/" + model + "/" + b + "/" + filename;
      var stem = filename.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]/g, "-");
      var branch = "add-" + stem + "-" + Date.now();
      var head = upstream === fork ? branch : fork.split("/")[0] + ":" + branch;
      var isUpdate = !!existing[mfr + "/" + model + "/" + b + "/" + filename];
      var commitMsg = ($("#u-msg").value.trim() || (isUpdate ? "Update " : "Add ") + repoPath).slice(0, 200);
      var encPath = repoPath.split("/").map(encodeURIComponent).join("/");
      var encBranch = encodeURIComponent(branch);
      var prBody = [
        "Contribution of `" + repoPath + "` via the dbchub upload dialog.",
        "",
        "- Manufacturer: `" + mfr + "`",
        "- Model / platform: `" + model + "`",
        "- Bus: `" + b + "`",
        "- File size: " + fmtSize(file.size),
        "",
        "The contributor confirmed they have the right to publish this file under CC BY 4.0 and that it contains no confidential or personal data."
      ].join("\n");

      submit.disabled = true;
      resetSteps();
      msg("ok", "Working…");
      var step = 0;
      function advance() { setStep(step, "done"); step++; if (step < STEP_NAMES.length) setStep(step, "doing"); }
      setStep(0, "doing");

      readFile(file).then(function (content) {
        advance();
        return gh("GET", "/repos/" + fork + "/git/ref/heads/" + encodeURIComponent(base), token).then(function (r) {
          if (r.status === 401) throw new Error("GitHub rejected the token (401). Check that it is valid and has public_repo scope.");
          if (r.status !== 200) throw new Error("Cannot read base branch '" + base + "' on " + fork + " (" + (r.data.message || r.status) + "). Is the fork name correct?");
          return gh("POST", "/repos/" + fork + "/git/refs", token, { ref: "refs/heads/" + branch, sha: r.data.object.sha });
        }).then(function (r) {
          if (r.status !== 201) throw new Error("Cannot create branch (" + (r.data.message || r.status) + ").");
          advance();
          return gh("GET", "/repos/" + fork + "/contents/" + encPath + "?ref=" + encBranch, token);
        }).then(function (r) {
          var payload = { message: commitMsg, content: content, branch: branch };
          if (r.status === 200 && r.data.sha) payload.sha = r.data.sha; // update existing
          return gh("PUT", "/repos/" + fork + "/contents/" + encPath, token, payload);
        }).then(function (r) {
          if (r.status !== 200 && r.status !== 201) throw new Error("Cannot commit file (" + (r.data.message || r.status) + ").");
          advance();
          return gh("POST", "/repos/" + upstream + "/pulls", token, { title: commitMsg, head: head, base: base, body: prBody });
        }).then(function (r) {
          if (r.status !== 201) throw new Error("Cannot open pull request (" + (r.data.message || r.status) + ").");
          advance();
          var res = msg("ok", "✅ Pull request opened: ");
          if (isHttps(r.data.html_url)) {
            var a = el("a", null, r.data.html_url);
            a.href = r.data.html_url; a.target = "_blank"; a.rel = "noopener noreferrer";
            res.appendChild(a);
          }
        });
      }).catch(function (err) {
        if (step < STEP_NAMES.length) setStep(step, "failed");
        msg("err", "⚠ " + (err && err.message ? err.message : "Upload failed."));
      }).then(function () {
        submit.disabled = false;
      });
    });
  }

  /* ---------------- boot ---------------- */
  initUpload();
  if (CFG.page === "browse") initBrowse();
  openDetailFromHash();
})();
