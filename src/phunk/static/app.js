// phunk ui: plain JS, no build step. Talks to the local server's /api/*.
"use strict";

const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const WINDOWS = [6, 24, 72, 168];
const WINDOW_NAMES = { 6: "6 hours", 24: "24 hours", 72: "3 days", 168: "7 days" };

const store = {
  get(k, fallback) {
    try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
  sget(k, fallback) {
    try { const v = sessionStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  sset(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const state = {
  me: null,
  who: new URLSearchParams(location.search).get("as") || "",
  hours: 24,
  offset: 0,
  hidden: new Set(store.get("hiddenApps", [])),
  apps: [],
  people: [],
  dismissed: new Set(store.sget("dismissed", [])),
  channels: new Set(),
  feedSeq: 0,
};

// --------------------------------------------------------------- helpers

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  let data = {};
  try { data = await r.json(); } catch { /* not JSON */ }
  if (!r.ok) {
    const err = new Error(data.error || `HTTP ${r.status}`);
    err.readOnly = !!data.read_only;
    throw err;
  }
  return data;
}

let toastTimer;
function toast(msg, ms = 4000) {
  const t = $("#toast");
  t.replaceChildren(...codeify(msg));
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

// "`phunk key add`" -> <code>phunk key add</code>
function codeify(text) {
  return String(text).split("`").map((part, i) => (i % 2 ? h("code", {}, part) : part));
}

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

function avatar(user) {
  const letter = (user.display_name || user.username || "?").trim().charAt(0).toUpperCase() || "?";
  const tone = `avatar tone${(Number(user.fid) || 0) % 5}`;
  if (user.pfp && /^https:\/\//.test(user.pfp)) {
    const img = h("img", { class: "avatar", src: user.pfp, alt: "", loading: "lazy", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => img.replaceWith(h("div", { class: tone, "aria-hidden": "true" }, letter)));
    return img;
  }
  return h("div", { class: tone, "aria-hidden": "true" }, letter);
}

function handle(user) {
  return user.username ? `@${user.username}` : `fid ${user.fid}`;
}

function needKey() {
  toast(state.me && state.me.help ? state.me.help : "Read-only. Run `phunk key add`, then restart `phunk ui`.", 7000);
}

const IMAGE = /\.(png|jpe?g|gif|webp|avif)(\?|$)|imagedelivery\.net|imgur\.com\/\w+\.\w+$/i;

// ------------------------------------------------------------------ feed

function castCard(c) {
  const ref = { fid: c.fid, hash: c.hash };
  const meta = h("div", { class: "meta" },
    h("span", { class: "name" }, c.author.display_name || handle(c.author)),
    c.author.username && c.author.display_name ? h("span", { class: "muted" }, handle(c.author)) : null,
    h("span", { class: "muted", title: new Date(c.ts * 1000).toLocaleString() }, ago(c.ts)),
    h("span", { class: "chip app", title: "app that signed this cast" }, c.app),
    c.channel ? h("span", { class: "chip channel", title: c.channel_url }, c.channel.includes("://") ? c.channel : `/${c.channel}`) : null,
  );
  const replyTo = c.reply_to
    ? h("div", { class: "muted small" }, `replying to ${c.reply_to.username ? "@" + c.reply_to.username : "fid " + c.reply_to.fid}`)
    : null;
  const embeds = h("div", { class: "embeds" }, c.embeds.map((e) => {
    if (e.startsWith("cast:")) return h("span", { class: "muted small" }, "quoted cast");
    if (!/^https?:\/\//.test(e)) return null;
    if (IMAGE.test(e) && e.startsWith("https://")) {
      return h("a", { href: e, target: "_blank", rel: "noopener noreferrer" },
        h("img", { src: e, alt: "image", loading: "lazy", referrerpolicy: "no-referrer" }));
    }
    return h("a", { href: e, target: "_blank", rel: "noopener noreferrer" }, e);
  }));

  const toggle = (kind) => {
    const btn = h("button", { type: "button", class: `ghost ${kind}`, "aria-pressed": "false" }, kind === "like" ? "Like" : "Recast");
    btn.addEventListener("click", async () => {
      if (!state.me.can_write) return needKey();
      const on = btn.classList.contains("on");
      btn.disabled = true;
      try {
        await api("/api/react", { action: on ? `un${kind}` : kind, cast: ref });
        btn.classList.toggle("on", !on);
        btn.setAttribute("aria-pressed", String(!on));
        btn.textContent = !on ? (kind === "like" ? "Liked" : "Recasted") : (kind === "like" ? "Like" : "Recast");
      } catch (e) { toast(e.message); } finally { btn.disabled = false; }
    });
    return btn;
  };

  const body = h("div", {}, meta, replyTo, c.text ? h("div", { class: "text" }, c.text) : null, embeds);
  const replyBtn = h("button", { type: "button", class: "ghost reply" }, "Reply");
  replyBtn.addEventListener("click", () => {
    if (!state.me.can_write) return needKey();
    const open = body.querySelector(".reply-box");
    if (open) { open.remove(); return; }
    const ta = h("textarea", { rows: "2", maxlength: "1024", placeholder: `Reply to ${handle(c.author)}`, "aria-label": "Reply" });
    const send = h("button", { type: "submit" }, "Reply");
    const form = h("form", { class: "reply-box" }, ta, h("div", { class: "row" }, send));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!ta.value.trim()) return;
      send.disabled = true;
      try {
        await api("/api/cast", { text: ta.value, reply_to: ref });
        form.remove();
        toast("Reply sent.");
      } catch (e) { toast(e.message); send.disabled = false; }
    });
    body.querySelector(".actions").after(form);
    ta.focus();
  });
  body.append(h("div", { class: "actions" }, replyBtn, toggle("like"), toggle("recast")));
  return h("article", { class: "card cast" }, avatar(c.author), body);
}

function renderApps() {
  const box = $("#apps");
  box.replaceChildren(...state.apps.map((a) => {
    const cb = h("input", { type: "checkbox", value: a.name });
    cb.checked = !state.hidden.has(a.name.toLowerCase());
    cb.addEventListener("change", () => {
      const k = a.name.toLowerCase();
      if (cb.checked) state.hidden.delete(k); else state.hidden.add(k);
      store.set("hiddenApps", [...state.hidden]);
      loadFeed(true);
    });
    return h("label", {}, cb, `${a.name} (${a.count})`);
  }));
  const hiddenHere = state.apps.filter((a) => state.hidden.has(a.name.toLowerCase())).length;
  $("#apps-summary").textContent = state.apps.length
    ? `${state.apps.length} seen${hiddenHere ? `, ${hiddenHere} hidden` : ""}` : "";
}

function setAllApps(show) {
  for (const a of state.apps) {
    if (show) state.hidden.delete(a.name.toLowerCase()); else state.hidden.add(a.name.toLowerCase());
  }
  store.set("hiddenApps", [...state.hidden]);
  loadFeed(true);
}

function canBrowse() {
  return state.who || (state.me && state.me.fid !== null);
}

async function loadFeed(reset, refresh = false) {
  const feed = $("#feed");
  const more = $("#more");
  const seq = ++state.feedSeq;
  if (!canBrowse()) {
    feed.replaceChildren(h("div", { class: "card empty" }, "Pick an account to browse as, for example dwr."));
    more.hidden = true;
    return;
  }
  if (reset) {
    state.offset = 0;
    feed.replaceChildren(h("div", { class: "card empty" }, h("span", { class: "spinner" }),
      "Reading the latest casts of everyone followed. Large follow lists take a minute on a shared node."));
  }
  more.disabled = true;
  $("#feed-status").textContent = "";
  const q = new URLSearchParams({ hours: state.hours, offset: state.offset, limit: PAGE });
  if (state.who) q.set("who", state.who);
  if (state.hidden.size) q.set("hide", [...state.hidden].join(","));
  if (refresh) q.set("refresh", "1");
  try {
    const d = await api(`/api/feed?${q}`);
    if (seq !== state.feedSeq) return;
    if (reset) feed.replaceChildren();
    state.apps = d.apps;
    renderApps();
    feed.append(...d.casts.map(castCard));
    for (const c of d.casts) if (c.channel && !c.channel.includes("://")) state.channels.add(c.channel);
    renderChannelPicks();
    state.offset = d.offset + d.casts.length;
    const label = WINDOW_NAMES[state.hours];
    $("#feed-status").textContent = `${d.total} of ${d.all} casts, last ${label}`;
    if (!d.total) {
      feed.replaceChildren(h("div", { class: "card empty" },
        d.all ? "Every cast in this window is from a hidden app." : "No casts in this window. Try a longer one."));
    }
    const wider = WINDOWS.find((w) => w > state.hours);
    if (d.more) { more.textContent = "Load more"; more.hidden = false; more.dataset.mode = "more"; }
    else if (wider && d.all) {
      more.textContent = `Load older (widen to ${WINDOW_NAMES[wider]})`;
      more.hidden = false; more.dataset.mode = "wider";
    } else more.hidden = true;
  } catch (e) {
    if (seq !== state.feedSeq) return;
    feed.replaceChildren(h("div", { class: "card empty" }, `Could not load the feed: ${e.message}`));
    more.hidden = true;
  } finally {
    more.disabled = false;
  }
}

// ---------------------------------------------------------------- people

function renderPeople() {
  const box = $("#people");
  const shown = state.people.filter((p) => !state.dismissed.has(p.fid)).slice(0, 10);
  if (!shown.length) {
    box.replaceChildren(h("p", { class: "empty" }, state.people.length ? "No more suggestions this session." : "No suggestions yet."));
    return;
  }
  box.replaceChildren(...shown.map((p) => {
    const follow = h("button", { type: "button", class: "small" }, "Follow");
    follow.addEventListener("click", async () => {
      if (!state.me.can_write) return needKey();
      follow.disabled = true;
      try {
        await api("/api/follow", { fid: p.fid, action: "follow" });
        state.people = state.people.filter((x) => x.fid !== p.fid);
        renderPeople();
        toast(`Following ${handle(p)}.`);
      } catch (e) { toast(e.message); follow.disabled = false; }
    });
    const nope = h("button", { type: "button", class: "ghost small" }, "Not interested");
    nope.addEventListener("click", () => {
      state.dismissed.add(p.fid);
      store.sset("dismissed", [...state.dismissed]);
      renderPeople();
    });
    return h("div", { class: "person" }, avatar(p), h("div", {},
      h("div", { class: "meta" }, h("span", { class: "name" }, p.display_name || handle(p)),
        p.username && p.display_name ? h("span", { class: "muted" }, handle(p)) : null),
      p.bio ? h("div", { class: "bio" }, p.bio.length > 140 ? p.bio.slice(0, 140) + "…" : p.bio) : null,
      h("ul", {}, p.reasons.map((r) => h("li", { class: /^Followed by/.test(r) ? "graph" : /^Both active in/.test(r) ? "channel" : null }, r))),
      h("div", { class: "row" }, follow, nope)));
  }));
}

async function loadPeople(refresh = false) {
  const box = $("#people");
  if (!canBrowse()) { box.replaceChildren(h("p", { class: "empty" }, "Pick an account first.")); return; }
  box.replaceChildren(h("p", { class: "empty" }, h("span", { class: "spinner" }), "Looking at who your follows follow…"));
  const q = new URLSearchParams();
  if (state.who) q.set("who", state.who);
  if (refresh) q.set("refresh", "1");
  try {
    const d = await api(`/api/suggestions?${q}`);
    state.people = d.people;
    renderPeople();
  } catch (e) {
    box.replaceChildren(h("p", { class: "empty" }, `Could not load suggestions: ${e.message}`));
  }
}

// ------------------------------------------------------------------ setup

function setHours(v) {
  state.hours = v;
  for (const r of document.querySelectorAll("#hours input")) r.checked = Number(r.value) === v;
}

function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  $("#theme").textContent = `Theme: ${t}`;
}

// Phones show one pane at a time; wide screens show them all.
const narrow = () => matchMedia("(max-width: 800px)").matches;

function setTab(name) {
  if (name === "compose" && state.me && !state.me.can_write) { needKey(); return; }
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const p of document.querySelectorAll(".pane")) p.classList.toggle("active", p.dataset.pane === name);
  if (name === "compose" && narrow()) $("#compose-text").focus();
}

// Channels seen in the feed, offered as one-tap picks under the composer.
function renderChannelPicks() {
  const box = $("#compose-channels");
  const input = $("#compose-channel");
  const names = [...state.channels].slice(0, 8);
  box.hidden = !names.length;
  box.replaceChildren(...names.map((n) => {
    const b = h("button", { type: "button", class: "pick", "aria-pressed": String(input.value === n) }, `/${n}`);
    b.addEventListener("click", () => {
      input.value = input.value === n ? "" : n;
      renderChannelPicks();
    });
    return b;
  }));
}

// The You screen: who this client reads as, and whether it can post.
function renderAccount() {
  const me = state.me;
  const box = $("#account");
  const other = state.who && (!me.user || state.who.replace(/^@/, "") !== me.user.username) ? state.who.replace(/^@/, "") : "";
  if (!me.user && !other) { box.hidden = true; return; }
  const rows = [];
  if (me.user) {
    rows.push(h("div", { class: "person-row" }, avatar(me.user), h("div", {},
      h("div", { class: "name" }, me.user.display_name || handle(me.user)),
      h("div", { class: "muted small mono" }, `${handle(me.user)} · fid ${me.user.fid}`)),
      h("span", { class: me.can_write ? "chip ok" : "chip warn" }, me.can_write ? "can post" : "read-only")));
  }
  if (other) rows.push(h("p", { class: "muted small" }, `Browsing ${/^\d+$/.test(other) ? "fid " + other : "@" + other}, read-only.`));
  box.replaceChildren(...rows);
  box.hidden = false;
}

function renderWho() {
  const me = state.me;
  const who = $("#who");
  if (state.who && (!me.user || state.who.replace(/^@/, "") !== me.user.username)) {
    who.textContent = `browsing as ${/^\d+$/.test(state.who) ? "fid " + state.who : "@" + state.who.replace(/^@/, "")} · read-only`;
  } else if (me.user) {
    who.textContent = `${handle(me.user)} · ${me.can_write ? "can post" : "read-only"}`;
  } else who.textContent = "read-only";
  renderAccount();
}

async function init() {
  let theme = store.get("theme", "auto");
  applyTheme(theme);
  $("#theme").addEventListener("click", () => {
    theme = { auto: "light", light: "dark", dark: "auto" }[theme] || "auto";
    store.set("theme", theme);
    applyTheme(theme);
  });
  for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => setTab(b.dataset.tab));
  setTab("feed");

  try {
    state.me = await api("/api/me");
  } catch (e) {
    $("#feed").replaceChildren(h("div", { class: "card empty" }, `Could not reach the local server: ${e.message}`));
    return;
  }
  // Someone else's feed is read-only, even with a key.
  if (state.who && state.me.can_write && state.me.user && state.who.replace(/^@/, "") !== state.me.user.username
      && state.who !== String(state.me.fid)) state.me.can_write = false;

  if (!state.me.can_write) {
    const banner = $("#banner");
    banner.replaceChildren(...codeify(state.me.help || "Read-only. Run `phunk key add`, then restart `phunk ui`."));
    banner.hidden = false;
    $("#compose-pane").hidden = true;
    $("#browse").hidden = false;
    $("#browse-name").value = state.who || (state.me.user && state.me.user.username) || "";
  }
  renderWho();
  if (!canBrowse()) setTab("you");

  $("#browse").addEventListener("submit", (ev) => {
    ev.preventDefault();
    state.who = $("#browse-name").value.trim().replace(/^@/, "");
    const url = new URL(location.href);
    if (state.who) url.searchParams.set("as", state.who); else url.searchParams.delete("as");
    history.replaceState(null, "", url);
    renderWho();
    if (state.who) setTab("feed");
    loadFeed(true);
    loadPeople();
  });
  $("#compose-close").addEventListener("click", () => setTab("feed"));
  $("#compose-channel").addEventListener("input", renderChannelPicks);

  const text = $("#compose-text");
  text.addEventListener("input", () => {
    $("#compose-count").textContent = `${new TextEncoder().encode(text.value).length}/1024`;
  });
  $("#compose").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.me.can_write) return needKey();
    if (!text.value.trim()) return;
    const btn = $("#compose button[type=submit]");
    btn.disabled = true;
    try {
      await api("/api/cast", { text: text.value, channel: $("#compose-channel").value });
      text.value = "";
      $("#compose-count").textContent = "0/1024";
      toast("Cast sent.");
      if (narrow()) setTab("feed");
    } catch (e) { toast(e.message); } finally { btn.disabled = false; }
  });

  setHours(state.hours);
  $("#hours").addEventListener("change", (ev) => { setHours(Number(ev.target.value)); loadFeed(true); });
  $("#refresh").addEventListener("click", () => loadFeed(true, true));
  $("#more").addEventListener("click", () => {
    if ($("#more").dataset.mode === "wider") {
      const wider = WINDOWS.find((w) => w > state.hours);
      setHours(wider);
      loadFeed(true);
    } else loadFeed(false);
  });
  $("#apps-all").addEventListener("click", () => setAllApps(true));
  $("#apps-none").addEventListener("click", () => setAllApps(false));
  $("#people-refresh").addEventListener("click", () => loadPeople(true));

  loadFeed(true);
  loadPeople();
}

init();
