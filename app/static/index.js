(function () {
  const $ = (id) => document.getElementById(id);
  const sessionEl = $("session"), signout = $("signout"),
        loginView = $("loginView"), loginKey = $("loginKey"), loginBtn = $("loginBtn"), loginStatus = $("loginStatus"),
        appView = $("appView"),
        tabPublish = $("tabPublish"), tabPrompts = $("tabPrompts"),
        promptsView = $("promptsView"), promptList = $("promptList"),
        form = $("form"), drop = $("drop"), fileInput = $("file"),
        dzEmpty = $("dzEmpty"), dzFile = $("dzFile"), dzName = $("dzName"), dzSize = $("dzSize"), dzClear = $("dzClear"),
        slug = $("slug"), statusEl = $("status"),
        result = $("result"), link = $("resultLink"), openLink = $("openLink"),
        pagesEl = $("pages"), refresh = $("refresh"), rep = $("rep"),
        foldersEl = $("folders"), folderForm = $("folderForm"), folderSlug = $("folderSlug");

  let selectedFile = null, repSlug = null;
  let folders = [];
  let authed = false, activeTab = "publish";

  // Every API call carries X-HS-Auth so cookie auth is honored (and CSRF isn't).
  const api = (path, opts = {}) => {
    opts.headers = Object.assign({ "X-HS-Auth": "1" }, opts.headers || {});
    return fetch(path, opts);
  };
  const fmtSize = (n) => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
  function setStatus(m, isErr) { statusEl.textContent = m || ""; statusEl.classList.toggle("err", !!isErr); }
  function setLoginStatus(m, isErr) { loginStatus.textContent = m || ""; loginStatus.classList.toggle("err", !!isErr); }

  function friendly(st, data, ctx) {
    const m = {
      400: "Check the slug — letters, numbers, - and _ only.",
      401: "Session expired — sign in again.",
      409: "That slug is taken — use Replace on the existing page.",
      413: "That file is over the size limit.",
      507: "Storage is full (file limit reached).",
    };
    return m[st] || (data && data.detail ? "Error: " + data.detail : (ctx || "Request") + " failed (" + st + ").");
  }
  function onAuthLost() { showLogin(); setLoginStatus("Session expired — sign in again.", true); }

  // --- views ---------------------------------------------------------------
  // Two tabs share the masthead. "Publish" shows the login form or the app
  // depending on auth; "Prompts" is always reachable, signed in or not.
  function applyView() {
    const onPrompts = activeTab === "prompts";
    promptsView.hidden = !onPrompts;
    tabPublish.classList.toggle("active", !onPrompts);
    tabPrompts.classList.toggle("active", onPrompts);
    tabPublish.setAttribute("aria-selected", String(!onPrompts));
    tabPrompts.setAttribute("aria-selected", String(onPrompts));
    sessionEl.hidden = !authed;
    loginView.hidden = onPrompts || authed;
    appView.hidden = onPrompts || !authed;
  }
  function selectTab(tab) {
    activeTab = tab;
    applyView();
    if (tab === "publish" && !authed) loginKey.focus();
  }
  tabPublish.addEventListener("click", () => selectTab("publish"));
  tabPrompts.addEventListener("click", () => selectTab("prompts"));

  function showLogin() {
    authed = false;
    setStatus(""); result.hidden = true;
    applyView();
    if (activeTab === "publish") loginKey.focus();
  }
  function showApp() {
    authed = true;
    setLoginStatus("");
    applyView();
    loadPages();
  }

  (async function init() {
    try {
      const res = await api("/api/session");
      const data = await res.json();
      data.authenticated ? showApp() : showLogin();
    } catch (_) { showLogin(); }
  })();

  // --- login / logout ------------------------------------------------------
  loginView.addEventListener("submit", async (e) => {
    e.preventDefault();
    const key = loginKey.value.trim();
    if (!key) return setLoginStatus("Enter your API key.", true);
    loginBtn.disabled = true; setLoginStatus("Signing in…");
    try {
      const res = await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
      if (res.ok) { loginKey.value = ""; showApp(); }
      else if (res.status === 401) setLoginStatus("That key didn't match.", true);
      else setLoginStatus("Sign in failed (" + res.status + ").", true);
    } catch (_) { setLoginStatus("Network error — is the server reachable?", true); }
    finally { loginBtn.disabled = false; }
  });

  signout.addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (_) {}
    selectedFile = null; setFile(null); slug.value = "";
    showLogin();
  });

  // --- drop zone -----------------------------------------------------------
  function setFile(f) {
    selectedFile = f || null;
    if (f) {
      dzName.textContent = f.name; dzSize.textContent = fmtSize(f.size);
      dzEmpty.hidden = true; dzFile.hidden = false;
      if (!slug.value) {
        const base = f.name.replace(/\.html?$/i, "");
        if (/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(base)) slug.value = base;
      }
    } else {
      dzEmpty.hidden = false; dzFile.hidden = true; fileInput.value = "";
    }
  }
  drop.addEventListener("click", (e) => { if (e.target !== dzClear) fileInput.click(); });
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  fileInput.addEventListener("change", (e) => { if (e.target.files[0]) setFile(e.target.files[0]); });
  dzClear.addEventListener("click", (e) => { e.stopPropagation(); setFile(null); });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  drop.addEventListener("dragleave", (e) => { if (!drop.contains(e.relatedTarget)) drop.classList.remove("drag"); });
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drag"); const f = e.dataTransfer.files[0]; if (f) setFile(f); });

  // --- publish -------------------------------------------------------------
  function showResult(url) { link.textContent = url; link.href = url; openLink.href = url; result.hidden = false; }
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(link.textContent); setStatus("Copied to clipboard."); }
    catch (_) { setStatus("Couldn't copy — select the URL manually.", true); }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedFile) return setStatus("Choose an HTML file first.", true);
    $("submit").disabled = true; setStatus("Publishing…"); result.hidden = true;
    const fd = new FormData();
    fd.append("file", selectedFile, selectedFile.name);
    if (slug.value.trim()) fd.append("slug", slug.value.trim());
    try {
      const res = await api("/api/upload", { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) { setStatus(friendly(res.status, data, "Upload"), true); return; }
      setStatus("Published."); showResult(data.url);
      setFile(null); slug.value = "";
      loadPages();
    } catch (_) { setStatus("Network error — is the server reachable?", true); }
    finally { $("submit").disabled = false; }
  });

  // --- manage --------------------------------------------------------------
  function folderRow(item) {
    const row = document.createElement("div"); row.className = "folder-row";
    const a = document.createElement("a"); a.className = "folder-link"; a.href = item.url;
    a.target = "_blank"; a.rel = "noopener"; a.textContent = item.slug + " ↗";
    const count = document.createElement("span"); count.className = "folder-count";
    count.textContent = item.pages.length + (item.pages.length === 1 ? " page" : " pages");
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "mini"; copy.textContent = "Copy link";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(item.url); setStatus("Folder link copied."); }
      catch (_) { setStatus("Couldn't copy — open folder link to copy it.", true); }
    });
    const del = document.createElement("button"); del.type = "button"; del.className = "mini danger"; del.textContent = "Delete";
    del.addEventListener("click", () => delFolder(item));
    row.append(a, count, copy, del);
    return row;
  }

  function renderFolders() {
    foldersEl.replaceChildren();
    if (!folders.length) { foldersEl.innerHTML = '<p class="pmsg">No folders yet.</p>'; return; }
    folders.forEach((item) => foldersEl.appendChild(folderRow(item)));
  }

  async function setPageFolder(pageSlug, current, next) {
    const path = next ? "/api/folders/" + encodeURIComponent(next) + "/pages/" + encodeURIComponent(pageSlug)
      : "/api/folders/" + encodeURIComponent(current) + "/pages/" + encodeURIComponent(pageSlug);
    try {
      const res = await api(path, { method: next ? "PUT" : "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) { setStatus(friendly(res.status, data, "Move page"), true); return loadPages(); }
      setStatus(next ? "Page moved to " + next + "." : "Page removed from folder.");
      loadPages();
    } catch (_) { setStatus("Network error.", true); loadPages(); }
  }

  function rowEl(item) {
    const row = document.createElement("div");
    row.className = "prow"; row.dataset.slug = item.slug;
    const dot = document.createElement("span"); dot.className = "dot";
    const a = document.createElement("a"); a.className = "pslug"; a.href = item.url;
    a.target = "_blank"; a.rel = "noopener"; a.textContent = item.slug;
    const size = document.createElement("span"); size.className = "psize"; size.textContent = fmtSize(item.size);
    const acts = document.createElement("span"); acts.className = "pactions";
    const current = folders.find((folder) => folder.pages.some((page) => page.slug === item.slug));
    const select = document.createElement("select"); select.className = "folder-select";
    select.setAttribute("aria-label", "Folder for " + item.slug);
    const none = new Option("No folder", ""); select.add(none);
    folders.forEach((folder) => select.add(new Option(folder.slug, folder.slug)));
    select.value = current ? current.slug : "";
    select.addEventListener("change", () => setPageFolder(item.slug, current ? current.slug : "", select.value));
    const rb = document.createElement("button"); rb.type = "button"; rb.className = "mini"; rb.textContent = "Replace";
    rb.addEventListener("click", () => { repSlug = item.slug; rep.value = ""; rep.click(); });
    const db = document.createElement("button"); db.type = "button"; db.className = "mini danger"; db.textContent = "Delete";
    db.addEventListener("click", () => delPage(item.slug, row));
    acts.append(select, rb, db);
    row.append(dot, a, size, acts);
    return row;
  }

  async function loadPages() {
    pagesEl.innerHTML = '<p class="pmsg">Loading…</p>';
    try {
      const [res, folderRes] = await Promise.all([api("/api/files?limit=1000"), api("/api/folders")]);
      if (res.status === 401 || folderRes.status === 401) { onAuthLost(); return; }
      const data = await res.json().catch(() => ({}));
      const folderData = await folderRes.json().catch(() => ({}));
      if (!res.ok || !folderRes.ok) { pagesEl.innerHTML = '<p class="pmsg">Couldn\'t load pages or folders.</p>'; return; }
      folders = folderData.folders || [];
      renderFolders();
      const files = data.files || [];
      if (!files.length) { pagesEl.innerHTML = '<p class="pmsg">No pages yet — upload one above.</p>'; return; }
      pagesEl.innerHTML = "";
      files.forEach((item) => pagesEl.appendChild(rowEl(item)));
      if (data.total > files.length) {
        const p = document.createElement("p"); p.className = "pmsg";
        p.textContent = "Showing " + files.length + " of " + data.total + ".";
        pagesEl.appendChild(p);
      }
    } catch (_) { pagesEl.innerHTML = '<p class="pmsg">Network error.</p>'; }
  }

  rep.addEventListener("change", async (e) => {
    const f = e.target.files[0];
    const target = repSlug; repSlug = null;
    if (!f || !target) return;
    setStatus("Updating " + target + "…");
    try {
      const res = await api("/api/files/" + encodeURIComponent(target), { method: "PUT", headers: { "Content-Type": "text/html" }, body: f });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) { setStatus(friendly(res.status, data, "Update"), true); return; }
      setStatus("Updated " + target + ".");
      const row = [...pagesEl.querySelectorAll(".prow")].find((item) => item.dataset.slug === target);
      if (row) { row.classList.add("flash"); setTimeout(() => row.classList.remove("flash"), 1200); }
      loadPages();
    } catch (_) { setStatus("Network error.", true); }
  });

  async function delPage(s, row) {
    if (!confirm('Delete "' + s + '"? This removes the page permanently.')) return;
    setStatus("Deleting " + s + "…");
    try {
      const res = await api("/api/files/" + encodeURIComponent(s), { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) { setStatus(friendly(res.status, data, "Delete"), true); return; }
      setStatus("Deleted " + s + ".");
      loadPages();
    } catch (_) { setStatus("Network error.", true); }
  }

  refresh.addEventListener("click", loadPages);

  folderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = folderSlug.value.trim();
    if (!name) return;
    try {
      const res = await api("/api/folders", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slug: name }) });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) return setStatus(friendly(res.status, data, "Create folder"), true);
      folderSlug.value = ""; setStatus("Folder created. Assign pages below, then share its link."); loadPages();
    } catch (_) { setStatus("Network error.", true); }
  });

  async function delFolder(item) {
    if (!confirm('Delete folder "' + item.slug + '"? Its pages will remain available.')) return;
    try {
      const res = await api("/api/folders/" + encodeURIComponent(item.slug), { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) return onAuthLost();
      if (!res.ok) return setStatus(friendly(res.status, data, "Delete folder"), true);
      setStatus("Folder deleted; pages remain available."); loadPages();
    } catch (_) { setStatus("Network error.", true); }
  }

  // --- prompts -------------------------------------------------------------
  const PROMPTS = [
    {
      tag: "Explain",
      title: "Explain unfamiliar code",
      desc: "Turn a subsystem into a page you read once.",
      body: `I don't understand how our rate limiter actually works. Read the relevant code and produce a single HTML explainer page: a diagram of the token-bucket flow, the 3–4 key code snippets annotated, and a "gotchas" section at the bottom. Optimize it for someone reading it once.`,
    },
    {
      tag: "Organize",
      title: "Prioritize a backlog",
      desc: "Draggable Now / Next / Later / Cut board.",
      body: `I need to reprioritize these 30 Linear tickets. Make me an HTML file with each ticket as a draggable card across Now / Next / Later / Cut columns. Pre-sort them by your best guess. Add a "copy as markdown" button that exports the final ordering with a one-line rationale per bucket.`,
    },
    {
      tag: "Build",
      title: "Edit a config safely",
      desc: "Form editor that warns on broken dependencies.",
      body: `Here's our feature flag config. Build a form-based editor for it, group flags by area, show dependencies between them, warn me if I enable a flag whose prerequisite is off. Add a "copy diff" button that gives me just the changed keys.`,
    },
    {
      tag: "Build",
      title: "Tune a prompt",
      desc: "Side-by-side editor with live template preview.",
      body: `I'm tuning this system prompt. Make a side-by-side editor: editable prompt on the left with the variable slots highlighted, three sample inputs on the right that re-render the filled template live. Add a character/token counter and a copy button.`,
    },
    {
      tag: "Explore",
      title: "Compare directions",
      desc: "Six labeled variations, side by side.",
      body: `I'm not sure what direction to take the onboarding screen. Generate 6 distinctly different approaches — vary layout, tone, and density — and lay them out as a single HTML file in a grid so I can compare them side by side. Label each with the tradeoff it's making.`,
    },
    {
      tag: "Plan",
      title: "Plan a feature",
      desc: "Readable plan with mockups and snippets.",
      body: `Create a thorough implementation plan in a HTML file, be sure to make some mockups, show data flow and add important code snippets I might want to review. Make it easy to read and digest.`,
    },
    {
      tag: "Review",
      title: "Review a PR",
      desc: "Annotated diff, findings by severity.",
      body: `Help me review this PR by creating an HTML artifact that describes it. I'm not very familiar with the streaming/backpressure logic so focus on that. Render the actual diff with inline margin annotations, color-code findings by severity and whatever else might be needed to convey the concept well.`,
    },
    {
      tag: "Prototype",
      title: "Tweak an animation",
      desc: "Live sliders for a checkout-button effect.",
      body: `I want to prototype a new checkout button, when clicked it does a play animation and then turns purple quickly. Create a HTML file with several sliders and options for me to try different options on this animation, give me a copy button to copy the parameters that worked well.`,
    },
  ];

  function copyPrompt(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
      btn.textContent = "Copied"; btn.classList.add("ok");
      setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("ok"); }, 1200);
    }).catch(() => {
      btn.textContent = "Copy failed";
      setTimeout(() => { btn.textContent = "Copy"; }, 1200);
    });
  }

  function renderPrompts() {
    promptList.innerHTML = "";
    PROMPTS.forEach((p) => {
      const card = document.createElement("div"); card.className = "pcard";
      const top = document.createElement("div"); top.className = "pcard-top";
      const tag = document.createElement("span"); tag.className = "ptag"; tag.textContent = p.tag;
      const btn = document.createElement("button"); btn.type = "button"; btn.className = "mini"; btn.textContent = "Copy";
      btn.addEventListener("click", () => copyPrompt(p.body, btn));
      top.append(tag, btn);
      const title = document.createElement("h3"); title.className = "pcard-title"; title.textContent = p.title;
      const desc = document.createElement("p"); desc.className = "pcard-desc"; desc.textContent = p.desc;
      const body = document.createElement("div"); body.className = "pcard-body"; body.textContent = p.body;
      card.append(top, title, desc, body);
      promptList.appendChild(card);
    });
  }

  renderPrompts();
})();
