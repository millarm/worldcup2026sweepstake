/* World Cup 2026 Sweepstake — frontend logic + GSAP animation.
 * Fetches /api/state and renders Groups, Knockouts, Leaderboard and Players.
 * GSAP is optional: every render works without it; animation is layered on top. */
(() => {
  "use strict";
  const GS = window.gsap;
  const hasGSAP = typeof GS !== "undefined";
  if (hasGSAP && window.ScrollTrigger) GS.registerPlugin(window.ScrollTrigger);

  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
  const el = (tag, cls, html) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  };
  const esc = (s) => String(s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  let STATE = null;
  let ownerByCountry = {};
  const ROUND_ORDER = ["Round of 32", "Round of 16", "Quarter-final", "Semi-final", "Final"];

  /* ------------------------------------------------------------------ fetch */
  function renderAll() {
    renderPot();
    renderFeed();
    renderGroups();
    renderThirds();
    renderScores();
    renderBracket();
    renderLeaderboard();
    renderPlayers();
    $("#footTournament").textContent = STATE.tournament;
  }

  // Set count-up numbers + leaderboard bars to their final values immediately
  // (used on refresh/admin updates, where the intro animation isn't re-run).
  function setNumbersInstant() {
    $$("[data-count]").forEach((n) => {
      n.textContent = (n.classList.contains("amt") ? "£" : "") + n.dataset.count;
    });
    $$(".lb-bar").forEach((b) => (b.style.width = b.dataset.w + "%"));
  }

  function hydrate(data) {
    STATE = data;
    ownerByCountry = Object.fromEntries((STATE.teams || []).map((t) => [t.country, t.owner_short]));
    renderAll();
  }

  async function load() {
    try {
      const res = await fetch("/api/state");
      hydrate(await res.json());
    } catch (e) {
      $("#feedStatus").textContent = "Could not reach the server.";
      return;
    }
    intro();
    // The backend polls the live results feed; mirror that on the client so the
    // page updates itself without a manual reload.
    setInterval(refresh, 60000);
  }

  async function refresh() {
    if (document.hidden) return; // don't poll a backgrounded tab
    try {
      const res = await fetch("/api/state");
      const data = await res.json();
      const before = STATE && STATE.feed ? STATE.feed.ran_at : null;
      hydrate(data);
      setNumbersInstant();
      if (STATE.prizes && STATE.prizes.champion) fireConfetti();
      if (before !== (STATE.feed && STATE.feed.ran_at)) flashUpdated();
    } catch (e) { /* keep last good state on a transient error */ }
  }

  function flashUpdated() {
    const node = $("#feedStatus");
    if (hasGSAP && node) GS.fromTo(node, { opacity: 0.3 }, { opacity: 1, duration: 0.8 });
  }

  const withOwner = (country) =>
    country ? `${esc(country)} <small>(${esc(ownerByCountry[country] || "?")})</small>` : "";

  /* -------------------------------------------------------------------- pot */
  function renderPot() {
    const pot = STATE.prize_pot;
    const strip = $("#potStrip");
    strip.innerHTML = "";
    const total = el("div", "chip total");
    total.innerHTML = `<div class="amt" data-count="${pot.total}">£0</div><div class="lbl">Total pot</div>`;
    strip.appendChild(total);
    pot.prizes.forEach((p) => {
      const isMind = /mind/i.test(p.label);
      const chip = el("div", "chip" + (isMind ? " mind" : ""));
      chip.innerHTML = `<div class="amt" data-count="${p.amount}">£0</div><div class="lbl">${esc(p.label)}</div>`;
      strip.appendChild(chip);
    });
  }

  function renderFeed() {
    const f = STATE.feed;
    const node = $("#feedStatus");
    if (!f) { node.innerHTML = "No results loaded yet — open ⚙︎ to pull the feed."; return; }
    const when = f.ran_at ? new Date(f.ran_at).toLocaleString() : "";
    node.innerHTML = `Last feed: <b>${esc(f.source)}</b> · ${f.updated} result(s) · ${esc(when)}`;
  }

  /* ----------------------------------------------------------------- groups */
  function renderGroups() {
    const grid = $("#groupGrid");
    grid.innerHTML = "";
    STATE.groups.forEach((g) => {
      const rows = STATE.standings[g] || [];
      const card = el("div", "card group-card reveal");
      let body = `<h3><span class="group-badge">${esc(g)}</span> Group ${esc(g)}</h3>
        <table class="standings"><thead><tr>
        <th class="pos"></th><th class="name">Team</th>
        <th>P</th><th>W</th><th>D</th><th>L</th><th>GF</th><th>GA</th><th>GD</th><th class="pts">Pts</th>
        </tr></thead><tbody>`;
      rows.forEach((r) => {
        const paidDot = `<span class="tagdot ${r.paid ? "" : "unpaid"}" title="${r.paid ? "Paid" : "Not paid"}"></span>`;
        body += `<tr class="${r.qualified ? "qual" : ""}">
          <td class="pos">${r.rank}</td>
          <td class="name"><span class="team">${esc(r.country)}</span>${paidDot}
            <div class="owner">${esc(r.owner_short || "")}</div></td>
          <td>${r.played}</td><td>${r.won}</td><td>${r.drawn}</td><td>${r.lost}</td>
          <td>${r.gf}</td><td>${r.ga}</td><td>${r.gd > 0 ? "+" : ""}${r.gd}</td>
          <td class="pts">${r.points}</td></tr>`;
      });
      body += "</tbody></table>";
      card.innerHTML = body;
      grid.appendChild(card);
    });
  }

  function renderThirds() {
    const row = $("#thirdsRow");
    row.innerHTML = "";
    (STATE.third_placed || []).forEach((t) => {
      const c = el("div", "card third-card reveal");
      c.innerHTML = `<div class="third-rank">${t.rank}</div>
        <div><div class="team">${esc(t.country)} <span class="owner">${esc(t.owner_short || "")}</span></div>
        <div class="owner">Group ${esc(t.group)} · ${t.points} pts · GD ${t.gd > 0 ? "+" : ""}${t.gd}</div></div>`;
      row.appendChild(c);
    });
  }

  /* ----------------------------------------------------------------- scores */
  // One match row. Played -> "score – score"; upcoming -> "vs" + kickoff.
  function scoreRow(home, away, hs, as_, homeWin, awayWin, homeSub, awaySub, meta, upcoming) {
    const center = upcoming
      ? `<div class="sr-vs">vs</div>`
      : `<div class="sr-score">${hs}<span>–</span>${as_}</div>`;
    const row = el("div", "score-row card" + (upcoming ? " upcoming" : ""));
    row.innerHTML = `
      <div class="sr-side home ${homeWin ? "win" : ""}">
        <div class="sr-name">${esc(home)}</div><div class="sr-sub">${homeSub}</div>
      </div>
      <div class="sr-center">${center}${meta ? `<div class="sr-meta">${esc(meta)}</div>` : ""}</div>
      <div class="sr-side away ${awayWin ? "win" : ""}">
        <div class="sr-name">${esc(away)}</div><div class="sr-sub">${awaySub}</div>
      </div>`;
    return row;
  }

  const ptsLabel = (p) => (p > 0 ? "+" : "") + p; // +3 / +1 / 0
  const ownerOf = (c) => ownerByCountry[c] || "?";

  // Flatten group fixtures + knockout ties into one comparable match shape.
  function allMatches() {
    const list = [];
    (STATE.fixtures || []).forEach((f) => list.push({
      kind: "group", home: f.home, away: f.away,
      played: f.played, hs: f.home_score, as: f.away_score, result: f.result,
      hPts: f.home_points, aPts: f.away_points, winner: null,
      date: f.date, dateLabel: f.date_label, ko: f.ko, tag: "Group " + f.group,
    }));
    (STATE.bracket || []).forEach((m) => list.push({
      kind: "ko", home: m.team1, away: m.team2, locked: m.teams_locked,
      played: m.score1 != null && m.score2 != null, hs: m.score1, as: m.score2,
      winner: m.winner, date: m.meta && m.meta.date, dateLabel: (m.meta && m.meta.date_label) || "",
      ko: (m.meta && m.meta.ko) || "", tag: m.round,
      pens: m.score1 != null && m.score1 === m.score2 && m.winner,
    }));
    return list;
  }

  const dtKey = (m) => `${m.date || "9999-99-99"}T${m.ko || "99:99"}`;

  function playedRow(m) {
    const when = [m.dateLabel, m.tag].filter(Boolean).join(" · ");
    if (m.kind === "group") {
      return scoreRow(m.home, m.away, m.hs, m.as, m.result === "H", m.result === "A",
        `${esc(ownerOf(m.home))} · ${ptsLabel(m.hPts)}`,
        `${esc(ownerOf(m.away))} · ${ptsLabel(m.aPts)}`, when);
    }
    return scoreRow(m.home, m.away, m.hs, m.as, m.winner === m.home, m.winner === m.away,
      esc(ownerOf(m.home)), esc(ownerOf(m.away)), when + (m.pens ? " · on penalties" : ""));
  }

  function upcomingRow(m) {
    const meta = [m.dateLabel, m.ko, m.tag].filter(Boolean).join(" · ");
    return scoreRow(m.home, m.away, null, null, false, false,
      esc(ownerOf(m.home)), esc(ownerOf(m.away)), meta, true);
  }

  function column(title, emptyText, rows) {
    const col = el("div", "scores-col reveal");
    col.appendChild(el("h3", "col-head", title));
    if (!rows.length) {
      col.appendChild(el("div", "card empty", emptyText));
    } else {
      rows.forEach((node) => col.appendChild(node));
    }
    return col;
  }

  function renderScores() {
    const wrap = $("#scores");
    wrap.innerHTML = "";
    const matches = allMatches();
    const played = matches.filter((m) => m.played);
    // Upcoming = not played, with confirmed teams. Group fixtures always count;
    // knockout ties only once their teams are locked (not a live projection).
    const upcoming = matches.filter((m) =>
      !m.played && m.home && m.away && (m.kind === "group" || m.locked));

    $("#scoresSummary").textContent =
      `${played.length} of 104 played · ${upcoming.length} coming up.`;

    // Most recent first; soonest first.
    played.sort((a, b) => dtKey(b).localeCompare(dtKey(a)));
    upcoming.sort((a, b) => dtKey(a).localeCompare(dtKey(b)));

    const cols = el("div", "scores-cols");
    cols.appendChild(column("🟢 Recent results", "No results yet — check back after kick-off.",
      played.map(playedRow)));
    cols.appendChild(column("📅 Upcoming fixtures", "No fixtures left — the tournament is complete!",
      upcoming.map(upcomingRow)));
    wrap.appendChild(cols);
  }

  /* ---------------------------------------------------------------- bracket */
  function slotHTML(name, score, isWin, isTbd, poolText) {
    const label = name ? withOwner(name) : `<span>${esc(poolText || "To be decided")}</span>`;
    return `<div class="slot ${isWin ? "win" : ""} ${isTbd ? "tbd" : ""}">
      <span class="nm">${label}</span><span class="sc">${score ?? ""}</span></div>`;
  }

  function tieCard(m) {
    const t1win = m.winner && m.winner === m.team1;
    const t2win = m.winner && m.winner === m.team2;
    const poolA = !m.team1 ? "Winner pending" : null;
    const poolB = !m.team2 ? (m.pool ? `3rd place · ${m.pool}` : "Winner pending") : null;
    const card = el("div", "card tie reveal" + (m.round === "Final" ? " final" : ""));
    const date = m.meta && m.meta.date_label ? `${esc(m.meta.date_label)} ${esc(m.meta.ko || "")}` : "";
    card.innerHTML =
      slotHTML(m.team1, m.score1, t1win, !m.team1, poolA) +
      slotHTML(m.team2, m.score2, t2win, !m.team2, poolB) +
      `<div class="meta">#${m.number}${date ? " · " + date : ""}</div>`;
    return card;
  }

  function renderBracket() {
    const wrap = $("#bracket");
    wrap.innerHTML = "";
    const byRound = {};
    STATE.bracket.forEach((m) => (byRound[m.round] = byRound[m.round] || []).push(m));

    ROUND_ORDER.forEach((round) => {
      const col = el("div", "round-col");
      col.appendChild(el("h4", null, round));
      (byRound[round] || []).forEach((m) => col.appendChild(tieCard(m)));
      // Park the third-place play-off beneath the Final.
      if (round === "Final" && byRound["Third Place"]) {
        col.appendChild(el("h4", null, "Third Place"));
        byRound["Third Place"].forEach((m) => col.appendChild(tieCard(m)));
      }
      wrap.appendChild(col);
    });

    const existing = $("#championBanner");
    if (existing) existing.remove(); // avoid stacking on re-render/refresh
    const champ = STATE.prizes && STATE.prizes.champion;
    if (champ) {
      const banner = el("div", "card champion-banner reveal");
      banner.id = "championBanner";
      banner.innerHTML = `<div class="lbl muted">🏆 Champions</div>
        <div class="who">${withOwner(champ)}</div>`;
      $("#panel-bracket").appendChild(banner);
      fireConfetti();
    }
  }

  /* ------------------------------------------------------------ leaderboard */
  function renderLeaderboard() {
    const board = $("#board");
    board.innerHTML = "";
    const rows = STATE.leaderboard || [];
    const max = Math.max(1, ...rows.map((r) => r.score));
    rows.forEach((r) => {
      const row = el("div", "card lb-row reveal" + (r.rank <= 3 ? " top" + r.rank : ""));
      const teams = r.teams.map((t) => t.country).join(", ");
      row.innerHTML = `<div class="rank">${r.rank}</div>
        <div><div class="who">${esc(r.owner)}</div>
          <div class="sub">${r.alive} alive · furthest: ${esc(r.furthest)} · ${esc(teams)}</div></div>
        <div class="score" data-count="${r.score}">0</div>
        <div class="lb-bar" data-w="${Math.round((r.score / max) * 100)}"></div>`;
      board.appendChild(row);
    });
    // Prize callout
    const prizes = STATE.prizes;
    if (prizes && (prizes.champion || prizes.runner_up)) {
      const note = el("div", "card lb-row reveal");
      note.style.gridTemplateColumns = "1fr";
      note.innerHTML = `<div class="sub">Prize money — ${prizes.awards
        .map((a) => `<b>${esc(a.label)}</b> £${a.amount}${a.recipient ? " → " + esc(a.recipient) : ""}`)
        .join(" · ")}</div>`;
      board.appendChild(note);
    }
  }

  /* --------------------------------------------------------------- players */
  function renderPlayers() {
    const wrap = $("#players");
    wrap.innerHTML = "";
    // Build owner -> teams with status from leaderboard progress.
    const byOwner = {};
    (STATE.leaderboard || []).forEach((o) => (byOwner[o.owner] = o));
    (STATE.people || []).forEach((p) => {
      const o = byOwner[p.name];
      const card = el("div", "card player-card reveal");
      let body = `<h3>${esc(p.name)}</h3>
        <div class="meta">${o ? `${o.alive} still in · ${o.score} pts` : "—"}</div>`;
      (o ? o.teams : []).forEach((t) => {
        body += `<div class="team-row ${t.eliminated ? "out" : ""}">
          <span class="dot"></span><span class="tn">${esc(t.country)}</span>
          <span class="st">${esc(t.eliminated ? "out" : t.stage)}</span></div>`;
      });
      card.innerHTML = body;
      wrap.appendChild(card);
    });
  }

  /* ---------------------------------------------------------------- tabs */
  function moveInk(btn) {
    const ink = $("#tabInk");
    if (!ink) return;
    ink.style.width = btn.offsetWidth - 24 + "px";
    ink.style.left = btn.offsetLeft + 12 + "px";
  }
  function setupTabs() {
    const tabs = $$(".tab");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.toggle("is-active", t === tab));
        moveInk(tab);
        const name = tab.dataset.tab;
        $$(".panel").forEach((p) => p.classList.toggle("is-active", p.id === "panel-" + name));
        const active = $("#panel-" + name);
        if (hasGSAP && active) {
          GS.fromTo(active.querySelectorAll(".reveal"),
            { y: 24, opacity: 0 }, { y: 0, opacity: 1, duration: 0.5, stagger: 0.04, ease: "power3.out", overwrite: true });
          if (window.ScrollTrigger) window.ScrollTrigger.refresh();
        }
      });
    });
    requestAnimationFrame(() => moveInk($(".tab.is-active")));
    window.addEventListener("resize", () => moveInk($(".tab.is-active")));
  }

  /* --------------------------------------------------------- GSAP intro */
  function countUp(node, to) {
    const prefix = node.classList.contains("amt") ? "£" : "";
    if (!hasGSAP) { node.textContent = prefix + to; return; }
    const obj = { v: 0 };
    GS.to(obj, { v: to, duration: 1.1, ease: "power2.out",
      onUpdate: () => (node.textContent = prefix + Math.round(obj.v)) });
  }

  function intro() {
    // Count-ups for pot chips.
    $$("[data-count]").forEach((n) => countUp(n, +n.dataset.count));
    // Leaderboard bars grow.
    if (hasGSAP) {
      $$(".lb-bar").forEach((b) =>
        GS.fromTo(b, { width: 0 }, { width: b.dataset.w + "%", duration: 1.1, ease: "power3.out", delay: 0.2 }));
    }
    if (!hasGSAP) return;

    const tl = GS.timeline({ defaults: { ease: "power3.out" } });
    tl.from('[data-anim="kicker"]', { y: 20, opacity: 0, duration: 0.6 })
      .from('[data-anim="title"]', { y: 60, opacity: 0, duration: 0.8, stagger: 0.12 }, "-=0.2")
      .from('[data-anim="subtitle"]', { y: 24, opacity: 0, duration: 0.6 }, "-=0.4")
      .from("#potStrip .chip", { y: 30, opacity: 0, duration: 0.5, stagger: 0.08 }, "-=0.3")
      .from(".scroll-cue", { opacity: 0, duration: 0.6 }, "-=0.2");

    GS.to(".brand-ball", { rotate: 360, repeat: -1, duration: 6, ease: "none" });
    GS.to(".scroll-cue", { y: 8, repeat: -1, yoyo: true, duration: 0.8 });
    GS.to(".blob", { scale: 1.25, repeat: -1, yoyo: true, duration: 8, stagger: 1.5, ease: "sine.inOut" });

    // Reveal cards on scroll for the active panel.
    if (window.ScrollTrigger) {
      $$(".panel.is-active .reveal").forEach((node) => {
        GS.from(node, {
          scrollTrigger: { trigger: node, start: "top 92%" },
          y: 28, opacity: 0, duration: 0.55,
        });
      });
    }
  }

  /* ------------------------------------------------------------- confetti */
  let confettiDone = false;
  function fireConfetti() {
    if (confettiDone || !hasGSAP) return;
    confettiDone = true;
    const canvas = $("#confetti");
    const ctx = canvas.getContext("2d");
    const resize = () => { canvas.width = innerWidth; canvas.height = innerHeight; };
    resize();
    const colors = ["#ffd54a", "#ff4d8d", "#36d8ff", "#2bff88", "#8a5bff"];
    const bits = Array.from({ length: 160 }, () => ({
      x: Math.random() * canvas.width, y: -20 - Math.random() * canvas.height,
      r: 4 + Math.random() * 6, c: colors[(Math.random() * colors.length) | 0],
      vy: 2 + Math.random() * 3, vx: -1 + Math.random() * 2, rot: Math.random() * 6.28,
    }));
    let t = 0;
    const tick = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      bits.forEach((b) => {
        b.y += b.vy; b.x += b.vx; b.rot += 0.1;
        ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.rot);
        ctx.fillStyle = b.c; ctx.fillRect(-b.r / 2, -b.r / 2, b.r, b.r * 1.6); ctx.restore();
        if (b.y > canvas.height + 20) b.y = -20;
      });
      if (++t < 320) requestAnimationFrame(tick);
      else ctx.clearRect(0, 0, canvas.width, canvas.height);
    };
    tick();
  }

  /* ---------------------------------------------------------------- admin */
  const ADMIN_KEY = "wc26-admin-pw"; // sessionStorage key for the unlocked password

  function setupAdmin() {
    const drawer = $("#adminDrawer");
    const lock = $("#adminLock");
    const controls = $("#adminControls");
    const lockMsg = $("#lockMsg");
    const msg = $("#adminMsg");

    const password = () => sessionStorage.getItem(ADMIN_KEY) || "";
    const headers = () => ({ "Content-Type": "application/json", "X-Admin-Token": password() });

    const showLocked = () => { controls.hidden = true; lock.hidden = false; $("#adminPassword").focus(); };
    const showUnlocked = () => { lock.hidden = true; controls.hidden = false; };

    const open = () => {
      drawer.classList.add("open");
      // Re-verify any stored password each time the drawer opens.
      if (password()) verify(password()).then((ok) => (ok ? showUnlocked() : showLocked()));
      else showLocked();
    };
    const close = () => drawer.classList.remove("open");
    $("#adminToggle").addEventListener("click", open);
    $("#adminClose").addEventListener("click", close);

    async function verify(pw) {
      try {
        const res = await fetch("/api/admin/login", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pw }),
        });
        return res.ok;
      } catch (e) { return false; }
    }

    lock.addEventListener("submit", async (e) => {
      e.preventDefault();
      lockMsg.textContent = "Checking…";
      const pw = $("#adminPassword").value;
      if (await verify(pw)) {
        sessionStorage.setItem(ADMIN_KEY, pw);
        $("#adminPassword").value = "";
        lockMsg.textContent = "";
        showUnlocked();
      } else {
        lockMsg.textContent = "⚠︎ Incorrect password";
      }
    });

    $("#lockBtn").addEventListener("click", () => {
      sessionStorage.removeItem(ADMIN_KEY);
      msg.textContent = "";
      showLocked();
    });

    const post = async (url, body, method = "POST") => {
      msg.textContent = "Working…";
      try {
        const res = await fetch(url, { method, headers: headers(), body: body ? JSON.stringify(body) : undefined });
        const data = await res.json();
        if (res.status === 401) { showLocked(); lockMsg.textContent = "⚠︎ Session expired — re-enter the password"; return; }
        if (!res.ok) throw new Error(data.error || res.statusText);
        hydrate(data);
        setNumbersInstant();
        if (STATE.prizes && STATE.prizes.champion) fireConfetti();
        msg.innerHTML = data.feed_summary
          ? `✓ Feed (${esc(data.feed_summary.source)}): ${data.feed_summary.updated} updated, ${data.feed_summary.unmatched.length} unmatched`
          : "✓ Saved";
      } catch (e) { msg.textContent = "⚠︎ " + e.message; }
    };
    $("#refreshFeed").addEventListener("click", () => post("/api/feed/refresh"));
    $("#gSave").addEventListener("click", () =>
      post("/api/results/group", { match: $("#gMatch").value.trim().toUpperCase(), home: +$("#gHome").value, away: +$("#gAway").value }));
    $("#kSave").addEventListener("click", () =>
      post("/api/results/ko", { match_no: +$("#kNo").value, score1: $("#kS1").value === "" ? null : +$("#kS1").value, score2: $("#kS2").value === "" ? null : +$("#kS2").value, override: $("#kOverride").value.trim() || null }));
    $("#resetAll").addEventListener("click", () => { if (confirm("Wipe all stored results?")) post("/api/admin/reset"); });
  }

  /* ----------------------------------------------------------- countdown */
  let _countdownTimes = [];   // sorted list of upcoming Date objects
  let _countdownTimer = null;

  async function fetchSchedule() {
    try {
      const res = await fetch("/api/feed/schedule");
      const data = await res.json();
      _countdownTimes = (data.schedule || [])
        .map((s) => new Date(s))
        .filter((d) => !isNaN(d))
        .sort((a, b) => a - b);
    } catch (e) {
      _countdownTimes = [];
    }
  }

  function tickCountdown() {
    const node = $("#nextRefresh");
    if (!node) return;

    const now = Date.now();
    // Drop times that have already passed.
    while (_countdownTimes.length && _countdownTimes[0].getTime() <= now) {
      _countdownTimes.shift();
    }

    if (!_countdownTimes.length) {
      node.textContent = "";
      return;
    }

    const diffMs = _countdownTimes[0].getTime() - now;
    if (diffMs < 0) { node.textContent = ""; return; }

    const totalSecs = Math.ceil(diffMs / 1000);
    if (totalSecs <= 15) {
      node.textContent = "⟳ Refreshing now…";
    } else {
      const days  = Math.floor(totalSecs / 86400);
      const hours = Math.floor((totalSecs % 86400) / 3600);
      const mins  = Math.floor((totalSecs % 3600) / 60);
      const secs  = totalSecs % 60;
      const parts = [];
      if (days  > 0) parts.push(`${days}d`);
      if (hours > 0) parts.push(`${hours}h`);
      if (mins  > 0) parts.push(`${mins}m`);
      parts.push(`${secs}s`);
      node.textContent = `⏱ Next update in ${parts.join(" ")}`;
    }
  }

  async function setupCountdown() {
    await fetchSchedule();
    tickCountdown();
    if (_countdownTimer) clearInterval(_countdownTimer);
    _countdownTimer = setInterval(() => {
      tickCountdown();
      // Re-fetch the schedule once per minute to stay in sync.
      const now = Date.now();
      if (!setupCountdown._lastFetch || now - setupCountdown._lastFetch >= 60000) {
        setupCountdown._lastFetch = now;
        fetchSchedule();
      }
    }, 1000);
  }

  /* ----------------------------------------------------------------- boot */
  document.addEventListener("DOMContentLoaded", () => {
    setupTabs();
    setupAdmin();
    load().then(() => setupCountdown());
  });
})();
