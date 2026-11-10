/* dbchub frontend — filtering, detail view, and browser-side uploads (-> PR).
 *
 * Security notes:
 *  - Catalog data (which includes signal/frame names parsed from contributed files)
 *    is treated as untrusted: it is only ever inserted with textContent / DOM APIs,
 *    never innerHTML, so markup in a file can't become live HTML.
 *  - Upload inputs are validated before they are placed into GitHub API URLs.
 *  - The GitHub token is used only against api.github.com and never logged.
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------------- config + catalog (inert JSON) ---------------- */
  var CFG = {
    upstream: (document.body.dataset.upstream || "").trim(),
    baseBranch: (document.body.dataset.baseBranch || "master").trim()
  };
  var CATALOG = [];
  try {
    var raw = ($("#dbchub-catalog") || {}).textContent;
    CATALOG = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(CATALOG)) CATALOG = [];
  } catch (e) { CATALOG = []; }
  var byPath = {};
  CATALOG.forEach(function (d) { byPath[d.path] = d; });

  /* ---------------- filtering ---------------- */
  var search = $("#search");
  var grid = $("#grid");
  var cards = $$(".card", grid);
  var shown = $("#shown");
  var empty = $("#empty");

  function checkedValues(cls) {
    return $$("." + cls + ":checked").map(function (el) { return el.value.toLowerCase(); });
  }
  function applyFilters() {
    var q = (search.value || "").trim().toLowerCase();
    var mfrs = checkedValues("f-mfr");
    var fmts = checkedValues("f-fmt");
    var visible = 0;
    cards.forEach(function (card) {
      var okText = !q || card.dataset.search.toLowerCase().indexOf(q) !== -1;
      var okMfr = !mfrs.length || mfrs.indexOf(card.dataset.mfr.toLowerCase()) !== -1;
      var okFmt = !fmts.length || fmts.indexOf(card.dataset.fmt.toLowerCase()) !== -1;
      var ok = okText && okMfr && okFmt;
      card.style.display = ok ? "" : "none";
      if (ok) visible++;
    });
    shown.textContent = visible;
    empty.style.display = visible ? "none" : "";
    grid.style.display = visible ? "" : "none";
  }
  search.addEventListener("input", applyFilters);
  $$(".f-mfr, .f-fmt").forEach(function (el) { el.addEventListener("change", applyFilters); });

  /* ---------------- modals ---------------- */
  function openModal(id) { $("#" + id).classList.add("open"); }
  function closeModal(el) { el.classList.remove("open"); }
  $$(".modal-backdrop").forEach(function (bd) {
    bd.addEventListener("click", function (e) { if (e.target === bd) closeModal(bd); });
    $$("[data-close]", bd).forEach(function (b) { b.addEventListener("click", function () { closeModal(bd); }); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") $$(".modal-backdrop.open").forEach(closeModal);
  });

  /* ---------------- detail view (DOM only, no innerHTML) ---------------- */
  function addRow(dl, key, val) {
    var dt = document.createElement("dt"); dt.textContent = key;
    var dd = document.createElement("dd"); dd.textContent = (val == null || val === "") ? "—" : String(val);
    dl.appendChild(dt); dl.appendChild(dd);
  }
  function isHttps(u) { return typeof u === "string" && /^https:\/\//i.test(u); }

  function showDetail(path) {
    var d = byPath[path];
    if (!d) return;
    $("#d-title").textContent = d.manufacturer + " / " + d.model + " — " + d.name;
    var c = d.counts || {};
    var dl = $("#d-kv");
    dl.textContent = "";
    addRow(dl, "Format", (d.format || "").toUpperCase());
    addRow(dl, "Bus", (d.bus || "").toUpperCase());
    addRow(dl, "Messages", c.messages);
    addRow(dl, "Signals", c.signals);
    addRow(dl, "Nodes", c.nodes);
    addRow(dl, "License", d.license || "CC-BY-4.0");
    if (d.source) {
      var dt = document.createElement("dt"); dt.textContent = "Source";
      var dd = document.createElement("dd");
      if (isHttps(d.source_url)) {
        var sa = document.createElement("a");
        sa.href = d.source_url; sa.target = "_blank"; sa.rel = "noopener noreferrer";
        sa.textContent = d.source;
        dd.appendChild(sa);
      } else { dd.textContent = d.source; }
      if (d.attribution) { dd.appendChild(document.createTextNode(" — " + d.attribution)); }
      dl.appendChild(dt); dl.appendChild(dd);
    }
    addRow(dl, "Checksum", d.checksum);
    addRow(dl, "Updated", d.updated);
    addRow(dl, "Commit", d.commit);
    addRow(dl, "Path", d.path);
    addRow(dl, "Status", d.valid ? "valid" : "invalid");

    var warn = $("#d-warn");
    warn.textContent = "";
    (d.warnings || []).forEach(function (w) {
      var li = document.createElement("li"); li.textContent = w; warn.appendChild(li);
    });

    // Only honor same-looking download/metadata links; drop anything not http(s).
    var dl2 = $("#d-download");
    if (isHttps(d.download) || (typeof d.download === "string" && d.download.indexOf("/") === 0)) {
      dl2.href = d.download; dl2.style.display = "";
    } else { dl2.removeAttribute("href"); dl2.style.display = "none"; }
    var md = $("#d-meta");
    if (isHttps(d.metadata_url) || (typeof d.metadata_url === "string" && d.metadata_url.indexOf("/") === 0)) {
      md.href = d.metadata_url; md.style.display = "";
    } else { md.removeAttribute("href"); md.style.display = "none"; }

    openModal("detail-modal");
  }

  cards.forEach(function (card) {
    card.addEventListener("click", function (e) {
      if (e.target.closest("a")) return; // let the download link work
      showDetail(card.dataset.path);
    });
  });

  /* ---------------- upload -> pull request ---------------- */
  var TOKEN_KEY = "dbchub.token";
  var SLUG = /^[a-z0-9][a-z0-9._-]*$/;              // manufacturer / model / bus
  var REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;  // owner/repo
  var FILE = /^[A-Za-z0-9][A-Za-z0-9._-]*\.(dbc|arxml|ldf|dbf|kcd|sym)$/i;

  var tokenInput = $("#u-token");
  var remember = $("#u-remember");
  try {
    var saved = localStorage.getItem(TOKEN_KEY);
    if (saved) { tokenInput.value = saved; remember.checked = true; }
  } catch (e2) {}

  $("#open-upload").addEventListener("click", function () { openModal("upload-modal"); });

  function gh(method, path, token, body) {
    return fetch("https://api.github.com" + path, {
      method: method,
      headers: {
        Authorization: "Bearer " + token,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
      },
      body: body ? JSON.stringify(body) : undefined
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
    for (var i = 0; i < bytes.length; i += chunk) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
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
    var el = $("#u-result");
    el.className = "result show " + kind;
    el.textContent = text;
    return el;
  }

  $("#upload-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var file = $("#u-file").files[0];
    var mfr = $("#u-mfr").value.trim().toLowerCase();
    var model = $("#u-model").value.trim().toLowerCase();
    var bus = $("#u-bus").value.trim().toLowerCase();
    var fork = $("#u-fork").value.trim();
    var token = tokenInput.value.trim();

    // ---- validation (also stops path traversal / URL injection) ----
    if (!file) return msg("err", "Choose a file to upload.");
    var filename = file.name.split(/[\\/]/).pop();
    if (!FILE.test(filename)) return msg("err", "File must be .dbc/.arxml/.ldf/.dbf/.kcd/.sym with a plain name.");
    if (!SLUG.test(mfr)) return msg("err", "Manufacturer: use lowercase letters, digits, dot, dash, underscore.");
    if (!SLUG.test(model)) return msg("err", "Model: use lowercase letters, digits, dot, dash, underscore.");
    if (!SLUG.test(bus)) return msg("err", "Bus: use lowercase letters, digits, dot, dash, underscore.");
    if (!REPO.test(fork)) return msg("err", "Fork must look like owner/repo.");
    if (!token) return msg("err", "A GitHub token is required.");
    if (CFG.upstream && !REPO.test(CFG.upstream)) return msg("err", "Site is misconfigured: upstream_repo is invalid.");

    try {
      if (remember.checked) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (e3) {}

    var upstream = CFG.upstream || fork;
    var base = CFG.baseBranch || "master";
    var repoPath = "databases/" + mfr + "/" + model + "/" + bus + "/" + filename;
    var stem = filename.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]/g, "-");
    var branch = "add-" + stem + "-" + Date.now();
    var head = upstream === fork ? branch : fork.split("/")[0] + ":" + branch;
    var commitMsg = ($("#u-msg").value.trim() || "Add " + repoPath).slice(0, 200);
    var encPath = repoPath.split("/").map(encodeURIComponent).join("/");
    var encBranch = encodeURIComponent(branch);

    var submit = $("#u-submit");
    submit.disabled = true;
    msg("ok", "Working… creating branch and commit on " + fork + ".");

    readFile(file).then(function (content) {
      return gh("GET", "/repos/" + fork + "/git/ref/heads/" + encodeURIComponent(base), token).then(function (r) {
        if (r.status !== 200) throw new Error("Cannot read base branch '" + base + "' on " + fork + " (" + (r.data.message || r.status) + ").");
        return gh("POST", "/repos/" + fork + "/git/refs", token, { ref: "refs/heads/" + branch, sha: r.data.object.sha });
      }).then(function (r) {
        if (r.status !== 201) throw new Error("Cannot create branch (" + (r.data.message || r.status) + ").");
        return gh("GET", "/repos/" + fork + "/contents/" + encPath + "?ref=" + encBranch, token);
      }).then(function (r) {
        var body = { message: commitMsg, content: content, branch: branch };
        if (r.status === 200 && r.data.sha) body.sha = r.data.sha; // update existing
        return gh("PUT", "/repos/" + fork + "/contents/" + encPath, token, body);
      }).then(function (r) {
        if (r.status !== 200 && r.status !== 201) throw new Error("Cannot commit file (" + (r.data.message || r.status) + ").");
        return gh("POST", "/repos/" + upstream + "/pulls", token, {
          title: commitMsg, head: head, base: base,
          body: "Automated contribution of `" + repoPath + "` via the dbchub frontend."
        });
      }).then(function (r) {
        if (r.status !== 201) throw new Error("Cannot open pull request (" + (r.data.message || r.status) + ").");
        var el = msg("ok", "✅ Pull request opened: ");
        if (isHttps(r.data.html_url)) {
          var a = document.createElement("a");
          a.href = r.data.html_url; a.target = "_blank"; a.rel = "noopener noreferrer";
          a.textContent = r.data.html_url;
          el.appendChild(a);
        }
      });
    }).catch(function (err) {
      msg("err", "⚠ " + (err && err.message ? err.message : "Upload failed."));
    }).then(function () {
      submit.disabled = false;
    });
  });
})();
