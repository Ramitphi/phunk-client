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
  cards: [],
  summary: null,
  dismissed: new Set(store.get("dismissed", [])),
  feedSeq: 0,
  peopleSeq: 0,
  feedLoaded: false,
  channels: new Set(),
  tab: "people",
  lastTab: "people",
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

// "dev" -> "/dev"; a channel URL stays as it is.
function chan(name) {
  return name.includes("://") ? name : `/${name}`;
}

function noteChannel(name) {
  if (name && !name.includes("://")) state.channels.add(name);
}

function handle(user) {
  return user.username ? `@${user.username}` : `fid ${user.fid}`;
}

function needKey() {
  if (state.me && state.me.can_write && !viewingSelf()) {
    toast("You're viewing someone else's people, read-only. Press \"Back to me\" to act.", 6000);
    return;
  }
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
    c.channel ? h("span", { class: "chip channel", title: c.channel_url }, chan(c.channel)) : null,
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
      if (!(state.me.can_write && viewingSelf())) return needKey();
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
    if (!(state.me.can_write && viewingSelf())) return needKey();
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
    feed.replaceChildren(h("div", { class: "card empty" }, "Look someone up first, for example dwr."));
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
    for (const c of d.casts) noteChannel(c.channel);
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

const KIND = {
  reconnect: ["Reconnect", "Someone you talked to"],
  circle: ["Your circle", "Many people you follow follow them"],
  bridge: ["Bridge", "A few mutuals, somewhere new"],
};

function viewingSelf() {
  const me = state.me;
  if (!state.who) return true;
  const w = state.who.replace(/^@/, "");
  return !!(me && me.user && (w === me.user.username || w === String(me.fid)));
}

function personCard(p) {
  const [label, hint] = KIND[p.kind] || [p.kind, ""];
  const canAct = state.me.can_write && viewingSelf();

  const follow = h("button", { type: "button", class: p.following ? "following" : null }, p.following ? "Following" : "Follow");
  if (p.following) follow.disabled = true;
  follow.addEventListener("click", async () => {
    if (!canAct) return needKey();
    follow.disabled = true;
    try {
      await api("/api/follow", { fid: p.fid, action: "follow" });
      p.following = true;
      follow.textContent = "Following";
      follow.classList.add("following");
      toast(`Following ${handle(p)}.`);
    } catch (e) { toast(e.message); follow.disabled = false; }
  });

  const latest = p.latest && p.latest[0];
  const hi = h("button", { type: "button", class: p.following ? "" : "ghost" }, "Say hi");
  const nope = h("button", { type: "button", class: "ghost" }, "Not now");
  const body = h("div", { class: "pbody" });

  hi.addEventListener("click", () => {
    if (!canAct) return needKey();
    const open = body.querySelector(".reply-box");
    if (open) { open.remove(); return; }
    const ta = h("textarea", { rows: "2", maxlength: "1024", "aria-label": `Say hi to ${handle(p)}`,
      placeholder: latest ? `Reply to ${handle(p)}'s latest cast` : `Mention ${handle(p)} in a new cast` });
    if (!latest && p.username) ta.value = `@${p.username} `;
    const send = h("button", { type: "submit" }, "Send");
    const form = h("form", { class: "reply-box" }, ta, h("div", { class: "row" }, send));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!ta.value.trim()) return;
      send.disabled = true;
      try {
        await api("/api/cast", latest ? { text: ta.value, reply_to: { fid: latest.fid, hash: latest.hash } } : { text: ta.value });
        form.remove();
        toast(`Sent to ${handle(p)}.`);
      } catch (e) { toast(e.message); send.disabled = false; }
    });
    body.querySelector(".pactions").after(form);
    ta.focus();
  });

  nope.addEventListener("click", () => {
    state.dismissed.add(p.fid);
    store.set("dismissed", [...state.dismissed]);
    renderPeople();
  });

  const app = p.app_now
    ? h("span", { class: "chip", title: "app that signed their latest cast" },
      p.app_before && p.app_before !== p.app_now ? `${p.app_before} → ${p.app_now}` : p.app_now)
    : null;
  const head = h("div", { class: "meta" },
    h("span", { class: "name" }, p.display_name || handle(p)),
    p.username && p.display_name ? h("span", { class: "muted" }, handle(p)) : null,
    app);
  const why = h("div", { class: "why" }, h("span", { class: `kind ${p.kind}`, title: hint }, label),
    h("span", {}, p.why.join(" ")));
  const previews = (p.latest || []).map((c) => h("div", { class: "preview" },
    h("span", { class: "muted small" }, `${ago(c.ts)}${c.channel ? " · " + chan(c.channel) : ""}${c.app ? " · " + c.app : ""}`),
    h("div", { class: "text clamp" }, c.text || (c.embeds && c.embeds.length ? "(a link or image)" : "(empty cast)"))));

  body.append(head,
    p.bio ? h("div", { class: "bio" }, p.bio.length > 160 ? p.bio.slice(0, 160) + "…" : p.bio) : null,
    why,
    previews.length ? h("div", { class: "previews" }, previews) : h("div", { class: "muted small" }, "No recent casts."),
    h("div", { class: "pactions" }, follow, hi, nope));
  return h("article", { class: `card person ${p.kind}` }, avatar(p), body);
}

function renderSummary() {
  const s = state.summary;
  const box = $("#summary");
  if (!s) { box.replaceChildren(); return; }
  const you = viewingSelf() ? "You" : (state.who.match(/^\d+$/) ? `fid ${state.who}` : `@${state.who.replace(/^@/, "")}`);
  const parts = [`${you} follow${you === "You" ? "" : "s"} ${s.following} people`];
  if (s.talked_to) parts.push(`talked with ${s.talked_to} in the last ${s.days} days`);
  let line = parts.join(" and ") + ".";
  const extra = [];
  if (s.moved) extra.push(`${s.moved} of them now post from a different app`);
  if (s.not_following) extra.push(`${s.not_following} ${you === "You" ? "you don't follow" : "they don't follow"}`);
  if (extra.length) line += " Of those, " + extra.join("; ") + ".";
  if (s.my_app) line += ` Posting from ${s.my_app === "own key" ? "their own key" : s.my_app}.`.replace("their own", you === "You" ? "your own" : "their own");
  box.replaceChildren(h("p", {}, line));
}

function renderPeople() {
  const box = $("#people");
  const shown = state.cards.filter((c) => !state.dismissed.has(c.fid)).slice(0, 15);
  const counts = { reconnect: 0, circle: 0, bridge: 0 };
  for (const c of shown) counts[c.kind] = (counts[c.kind] || 0) + 1;
  $("#people-status").textContent = shown.length
    ? `${shown.length} people · ${counts.reconnect} reconnect, ${counts.circle} circle, ${counts.bridge} bridge` : "";
  if (!shown.length) {
    box.replaceChildren(h("div", { class: "card empty" },
      state.cards.length ? "You've seen everyone for now. Refresh later." : "No one to suggest yet. Follow a few people first."));
    return;
  }
  box.replaceChildren(...shown.map(personCard));
}

async function loadPeople(refresh = false) {
  const box = $("#people");
  const seq = ++state.peopleSeq;
  if (!canBrowse()) {
    box.replaceChildren(h("div", { class: "card empty" }, "Look someone up first, for example dwr."));
    $("#summary").replaceChildren();
    return;
  }
  box.replaceChildren(h("div", { class: "card empty" }, h("span", { class: "spinner" }),
    "Reading who you talk to, who your follows follow, and which app each person posts from. About ten seconds."));
  const q = new URLSearchParams();
  if (state.who) q.set("who", state.who);
  if (refresh) q.set("refresh", "1");
  try {
    const d = await api(`/api/people?${q}`);
    if (seq !== state.peopleSeq) return;
    state.cards = d.cards;
    state.summary = d.summary;
    for (const c of d.cards) for (const l of c.latest || []) noteChannel(l.channel);
    renderChannelPicks();
    renderSummary();
    renderPeople();
  } catch (e) {
    if (seq !== state.peopleSeq) return;
    box.replaceChildren(h("div", { class: "card empty" }, `Could not load people: ${e.message}`));
  }
}

// ------------------------------------------------------------------ setup

function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  $("#theme").textContent = `Theme: ${t}`;
}

function setHours(v) {
  state.hours = v;
  for (const r of document.querySelectorAll("#hours input")) r.checked = Number(r.value) === v;
}

// Phones show one pane at a time from the bottom bar; wide screens keep the
// lookup on top and switch People / Casts / New cast with the top tabs.
const narrow = () => matchMedia("(max-width: 800px)").matches;

function setTab(name) {
  if (name === "compose" && state.me && !(state.me.can_write && viewingSelf())) { needKey(); return; }
  if (name !== "compose") state.lastTab = name;
  state.tab = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const p of document.querySelectorAll(".pane")) p.classList.toggle("active", p.dataset.pane === name);
  if (name === "casts" && !state.feedLoaded) { state.feedLoaded = true; loadFeed(true); }
  if (name === "compose") $("#compose-text").focus();
}

// Channels seen in casts and people's previews, as one-tap picks in Compose.
function renderChannelPicks() {
  const box = $("#compose-channels");
  const input = $("#compose-channel");
  const names = [...state.channels].slice(0, 8);
  box.hidden = !names.length;
  box.replaceChildren(...names.map((n) => {
    const b = h("button", { type: "button", class: "pick", "aria-pressed": String(input.value === n) }, chan(n));
    b.addEventListener("click", () => {
      input.value = input.value === n ? "" : n;
      renderChannelPicks();
    });
    return b;
  }));
}

// The You screen: who this client acts as, and whose people are shown.
function renderAccount() {
  const me = state.me;
  const box = $("#account");
  const rows = [];
  if (me.user) {
    rows.push(h("div", { class: "person-row" }, avatar(me.user), h("div", {},
      h("div", { class: "name" }, me.user.display_name || handle(me.user)),
      h("div", { class: "muted mono" }, `${handle(me.user)} · fid ${me.user.fid}`)),
      h("span", { class: me.can_write ? "chip ok" : "chip warn" }, me.can_write ? "can post" : "read-only")));
  }
  if (!viewingSelf()) {
    rows.push(h("p", { class: "muted small" },
      `Viewing ${/^\d+$/.test(state.who) ? "fid " + state.who : "@" + state.who.replace(/^@/, "")}'s people, read-only.`));
  }
  box.replaceChildren(...rows);
  box.hidden = !rows.length;
}

function renderWho() {
  const me = state.me;
  const who = $("#who");
  const self = viewingSelf();
  if (!self) {
    who.textContent = `viewing ${/^\d+$/.test(state.who) ? "fid " + state.who : "@" + state.who.replace(/^@/, "")} · read-only`;
  } else if (me.user) {
    who.textContent = `${handle(me.user)} · ${me.can_write ? "can post" : "read-only"}`;
  } else who.textContent = "read-only";
  $("#lookup-me").hidden = self || !me.user;
  if (!(me.can_write && self) && state.tab === "compose") setTab(state.lastTab);
  renderAccount();
}

function lookUp(name) {
  state.who = (name || "").trim().replace(/^@/, "");
  const url = new URL(location.href);
  if (state.who) url.searchParams.set("as", state.who); else url.searchParams.delete("as");
  history.replaceState(null, "", url);
  $("#lookup-name").value = state.who;
  renderWho();
  state.feedLoaded = false;
  loadPeople();
  if ($('.tabs button[data-tab="casts"]').getAttribute("aria-selected") === "true") { state.feedLoaded = true; loadFeed(true); }
  if (state.tab === "you") setTab("people");
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

  try {
    state.me = await api("/api/me");
  } catch (e) {
    $("#people").replaceChildren(h("div", { class: "card empty" }, `Could not reach the local server: ${e.message}`));
    return;
  }
  if (!state.me.can_write) {
    const banner = $("#banner");
    banner.replaceChildren(...codeify(state.me.help || "Read-only. Run `phunk key add`, then restart `phunk ui`."));
    banner.hidden = false;
  }
  $("#lookup-name").value = state.who;
  renderWho();

  $("#lookup").addEventListener("submit", (ev) => { ev.preventDefault(); lookUp($("#lookup-name").value); });
  $("#lookup-me").addEventListener("click", () => lookUp(""));

  $("#compose-close").addEventListener("click", () => setTab(state.lastTab));
  $("#compose-channel").addEventListener("input", renderChannelPicks);
  const text = $("#compose-text");
  text.addEventListener("input", () => {
    $("#compose-count").textContent = `${new TextEncoder().encode(text.value).length}/1024`;
  });
  $("#compose").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.me.can_write || !viewingSelf()) return needKey();
    if (!text.value.trim()) return;
    const btn = $("#compose button[type=submit]");
    btn.disabled = true;
    try {
      await api("/api/cast", { text: text.value, channel: $("#compose-channel").value });
      text.value = "";
      $("#compose-count").textContent = "0/1024";
      $("#compose-channel").value = "";
      renderChannelPicks();
      setTab(state.lastTab);
      toast("Cast sent.");
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

  setTab(!canBrowse() && narrow() ? "you" : "people");
  loadPeople();
}

init();
