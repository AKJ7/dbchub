/* dbchub frontend — filtering, detail view, and browser-side uploads (-> PR). */
(function () {
  "use strict";

  var CFG = window.DBCHUB || {};
  var CATALOG = Array.isArray(CFG.catalog) ? CFG.catalog : [];
  var byPath = {};
  CATALOG.forEach(function (d) { byPath[d.path] = d; });

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

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
      var hitText = !q || card.dataset.search.toLowerCase().indexOf(q) !== -1;
      var hitMfr = !mfrs.length || mfrs.indexOf(card.dataset.mfr.toLowerCase()) !== -1;
      var hitFmt = !fmts.length || fmts.indexOf(card.dataset.fmt.toLowerCase()) !== -1;
      var ok = hitText && hitMfr && hitFmt;
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

  /* ---------------- detail view ---------------- */
  function showDetail(path) {
    var d = byPath[path];
    if (!d) return;
    $("#d-title").textContent = d.manufacturer + " / " + d.model + " — " + d.name;
    var c = d.counts || {};
    var rows = [
      ["Format", (d.format || "").toUpperCase()],
      ["Bus", (d.bus || "—").toUpperCase()],
      ["Messages", c.messages], ["Signals", c.signals], ["Nodes", c.nodes],
      ["Checksum", d.checksum], ["Updated", d.updated || "—"],
      ["Commit", d.commit || "—"], ["Path", d.path],
      ["Status", d.valid ? "valid" : "invalid"]
    ];
    $("#d-kv").innerHTML = rows.map(function (r) {
      return "<dt>" + r[0] + "</dt><dd>" + (r[1] == null ? "—" : String(r[1])) + "</dd>";
    }).join("");
    var warn = $("#d-warn");
    warn.innerHTML = (d.warnings || []).map(function (w) { return "<li>" + w + "</li>"; }).join("");
    $("#d-download").href = d.download;
    $("#d-meta").href = d.metadata_url;
    openModal("detail-modal");
  }
  cards.forEach(function (card) {
    card.addEventListener("click", function () { showDetail(card.dataset.path); });
  });

  /* ---------------- upload -> pull request ---------------- */
  var TOKEN_KEY = "dbchub.token";
  var openUpload = $("#open-upload");
  var tokenInput = $("#u-token");
  var remember = $("#u-remember");
  try {
    var saved = localStorage.getItem(TOKEN_KEY);
    if (saved) { tokenInput.value = saved; remember.checked = true; }
  } catch (e) {}

  openUpload.addEventListener("click", function () { openModal("upload-modal"); });

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
        return { status: r.status, data: t ? JSON.parse(t) : {} };
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
      fr.onerror = rej;
      fr.readAsArrayBuffer(file);
    });
  }

  function setResult(kind, html) {
    var el = $("#u-result");
    el.className = "result show " + kind;
    el.innerHTML = html;
  }

  $("#upload-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var file = $("#u-file").files[0];
    var mfr = $("#u-mfr").value.trim().toLowerCase();
    var model = $("#u-model").value.trim().toLowerCase();
    var bus = $("#u-bus").value.trim().toLowerCase();
    var fork = $("#u-fork").value.trim();
    var token = tokenInput.value.trim();
    if (!file || !mfr || !model || !bus || !fork || !token) return;

    var upstream = CFG.upstream || fork;   // PR target; self if not configured
    var base = CFG.baseBranch || "master";
    var repoPath = "databases/" + mfr + "/" + model + "/" + bus + "/" + file.name;
    var stem = file.name.replace(/\.[^.]+$/, "");
    var branch = "add-" + stem + "-" + Date.now();
    var forkOwner = fork.split("/")[0];
    var head = upstream === fork ? branch : forkOwner + ":" + branch;
    var msg = $("#u-msg").value.trim() || "Add " + repoPath;

    try { localStorage.setItem(TOKEN_KEY, remember.checked ? token : ""); } catch (e2) {}

    var submit = $("#u-submit");
    submit.disabled = true;
    setResult("ok", "Working… creating branch and commit on <code>" + fork + "</code>.");

    readFile(file).then(function (content) {
      return gh("GET", "/repos/" + fork + "/git/ref/heads/" + base, token).then(function (r) {
        if (r.status !== 200) throw new Error("Cannot read base branch '" + base + "' on " + fork + ": " + (r.data.message || r.status));
        var baseSha = r.data.object.sha;
        return gh("POST", "/repos/" + fork + "/git/refs", token, { ref: "refs/heads/" + branch, sha: baseSha });
      }).then(function (r) {
        if (r.status !== 201) throw new Error("Cannot create branch: " + (r.data.message || r.status));
        // does the file already exist on the new branch? (update vs add)
        return gh("GET", "/repos/" + fork + "/contents/" + repoPath + "?ref=" + branch, token);
      }).then(function (r) {
        var body = { message: msg, content: content, branch: branch };
        if (r.status === 200 && r.data.sha) body.sha = r.data.sha;
        return gh("PUT", "/repos/" + fork + "/contents/" + repoPath, token, body);
      }).then(function (r) {
        if (r.status !== 200 && r.status !== 201) throw new Error("Cannot commit file: " + (r.data.message || r.status));
        return gh("POST", "/repos/" + upstream + "/pulls", token, {
          title: msg, head: head, base: base,
          body: "Automated contribution of `" + repoPath + "` via the dbchub frontend."
        });
      }).then(function (r) {
        if (r.status !== 201) throw new Error("Cannot open pull request: " + (r.data.message || r.status));
        setResult("ok", "✅ Pull request opened: <a href='" + r.data.html_url + "' target='_blank' rel='noopener'>" + r.data.html_url + "</a>");
      });
    }).catch(function (err) {
      setResult("err", "⚠ " + err.message);
    }).then(function () {
      submit.disabled = false;
    });
  });
})();
