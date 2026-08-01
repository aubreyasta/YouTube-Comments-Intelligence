/* Resonance local demo — frontend-only. No persistence; one mutable in-memory
   store mutated only inside demoApi. UI renderers treat store data as read-only. */
(function () {
"use strict";

/* ============================== Store ============================== */

const store = {
  sessions: new Map(),
  campaigns: new Map(),
  videos: new Map(),
  assets: new Map(),
  briefPoints: new Map(), // pointId -> BriefPoint
  runs: new Map(),
  reports: new Map(),     // runId -> Report
  artifacts: new Map(),
  assetData: new Map(),   // assetId -> Blob (uploaded file contents, for preview)
};

function uid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function demoError(code, message, field) {
  const e = new Error(message);
  e.code = code; e.field = field || null;
  return e;
}

function nowIso() { return new Date().toISOString(); }

function touchSession(sessionId) {
  const s = store.sessions.get(sessionId);
  if (s) s.updatedAt = nowIso();
}

/* ============================== Validation ============================== */

const MAX_UPLOAD = 10 * 1024 * 1024;
const UPLOAD_EXTS = ["pdf", "pptx", "docx", "png", "jpg", "jpeg", "webp"];
const IMAGE_EXTS = ["png", "jpg", "jpeg", "webp"];
const IMAGE_MIMES = ["image/png", "image/jpeg", "image/webp"];

function parseYouTubeUrl(raw) {
  const u = (raw || "").trim();
  if (!u) return { error: "Paste a YouTube link first." };
  if (/[?&]list=/.test(u)) {
    return { error: "Playlists are not supported. Add videos one at a time." };
  }
  let m;
  m = u.match(/^(?:https?:\/\/)?(?:www\.)?youtube\.com\/watch\?([^#]*)$/i);
  if (m) {
    const v = new URLSearchParams(m[1]).get("v");
    if (v && /^[\w-]{6,20}$/.test(v)) return { videoId: v, kind: "video" };
    return { error: "That watch link is missing a valid video ID." };
  }
  m = u.match(/^(?:https?:\/\/)?youtu\.be\/([\w-]{6,20})(?:[?#].*)?$/i);
  if (m) return { videoId: m[1], kind: "video" };
  m = u.match(/^(?:https?:\/\/)?(?:www\.)?youtube\.com\/shorts\/([\w-]{6,20})(?:[?#].*)?$/i);
  if (m) return { videoId: m[1], kind: "short" };
  return { error: "Only youtube.com/watch?v=, youtu.be/ and youtube.com/shorts/ links work." };
}

function requireName(value, fieldLabel) {
  const v = (value || "").trim();
  if (!v) throw demoError("validation", fieldLabel + " is required.", "name");
  return v;
}

/* ============================== Fixture content ============================== */

const DEMO_TITLES = [
  "Bola Rakyat — full film (60s)",
  "Kampung derby — behind the scenes",
  "Street football, one take (director's cut)",
  "Matchday in the gang — short",
];
const DEMO_CHANNELS = ["Nike Indonesia", "Nike Indonesia", "Bung Kicau", "Garuda Select"];

function demoVideoMeta(videoId, kind) {
  let h = 0;
  for (const ch of videoId) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const comments = 180 + (h % 3200);
  return {
    title: DEMO_TITLES[h % DEMO_TITLES.length],
    channel: DEMO_CHANNELS[h % DEMO_CHANNELS.length],
    commentCount: kind === "short" ? Math.max(60, Math.round(comments / 4)) : comments,
  };
}

const FIXTURE_POINTS = [
  { label: "Every kampung is a stadium", description: "The neighbourhood pitch framed as the real heart of Indonesian football." },
  { label: "Boots built for concrete, not turf", description: "Durability claim: outsole and upper engineered for street pitches." },
  { label: "Play like the whole city is watching", description: "Pressure and pride of playing in front of your own people." },
  { label: "Sepatu untuk semua — priced for the street", description: "Affordability promise: professional-grade boots within reach." },
  { label: "Made with recycled tarpaulin", description: "Materials story: upcycled tarpaulin panels in the upper." },
];

const FIXTURE_TRANSFERS = [
  { label: "Every kampung is a stadium", value: 47 },
  { label: "Play like the whole city is watching", value: 24 },
  { label: "Boots built for concrete, not turf", value: 14 },
  { label: "Sepatu untuk semua — priced for the street", value: 9 },
  { label: "Made with recycled tarpaulin", value: 2 },
];
const FIXTURE_THEMES = [
  { label: "Nostalgia for street football", value: 22 },
  { label: "Price and value", value: 18 },
  { label: "Will they survive concrete?", value: 15 },
  { label: "Casting and cameos", value: 12 },
  { label: "Craft and the music", value: 9 },
  { label: "Doubt about the brand's motive", value: 8 },
  { label: "Other", value: 16 },
];

const FIXTURE_EVIDENCE = {
  "Every kampung is a stadium": [
    { emotion: "Joy", author: "@raka_bdg", text: "lapangan depan rumah gue lebih rame dari GBK kalau sore 😂", likes: 1204 },
    { emotion: "Joy", author: "@tania.jkt", text: "bener sih, kita gak butuh rumput mahal buat main bola", likes: 612 },
    { emotion: "Joy", author: "@footyindo", text: "finally an ad that gets it. gang sempit pun jadi arena", likes: 488 },
    { emotion: "Neutral", author: "@dimas_sby", text: "tiap sore lapangan voli disulap jadi lapangan bola, itu kita banget", likes: 331 },
    { emotion: "Skeptical", author: "@nadriana", text: "bagus sih, tapi kampung gue lapangannya ya segitu-gitu aja dari dulu", likes: 96 },
  ],
  "Play like the whole city is watching": [
    { emotion: "Joy", author: "@angga92", text: "main bola di kampung tuh tekanannya lebih gede dari liga 😭", likes: 902 },
    { emotion: "Joy", author: "@kopitesub", text: "the whole gang watching you take a penalty, no pressure at all", likes: 410 },
    { emotion: "Neutral", author: "@lia.mks", text: "kalau kalah, diomongin seminggu sama tetangga", likes: 288 },
    { emotion: "Joy", author: "@pendekar_bola", text: "ini kenapa anak kampung mentalnya kuat", likes: 176 },
    { emotion: "Skeptical", author: "@rendra.f", text: "romantis banget narasinya, padahal ya main ya main aja", likes: 54 },
  ],
  "Boots built for concrete, not turf": [
    { emotion: "Skeptical", author: "@futsaljak", text: "beton itu pembunuh sepatu, bertahan berapa lama nih?", likes: 744 },
    { emotion: "Neutral", author: "@oktavian_d", text: "kalau outsole-nya beneran tahan beton gue beli", likes: 502 },
    { emotion: "Skeptical", author: "@winger11", text: "my last pair lasted two months on the same pitch lol", likes: 233 },
    { emotion: "Neutral", author: "@salsa.m", text: "ada yang udah nyoba di lapangan futsal semen?", likes: 121 },
    { emotion: "Joy", author: "@gerry.t", text: "solnya tebel banget pas close up, keliatan niat", likes: 88 },
  ],
  "Sepatu untuk semua — priced for the street": [
    { emotion: "Skeptical", author: "@harga.jujur", text: "sepatu untuk semua tapi harganya 2 juta 🙂", likes: 1880 },
    { emotion: "Skeptical", author: "@rakyatfc", text: "kalau bener buat rakyat, harganya jangan gaji sebulan", likes: 964 },
    { emotion: "Neutral", author: "@diskonhunter", text: "nunggu diskon 11.11 aja deh", likes: 402 },
    { emotion: "Skeptical", author: "@bang_jefri", text: "worth it kalau tahan setahun, kalau nggak ya mahal", likes: 190 },
    { emotion: "Joy", author: "@mamah_muda_fc", text: "anak gue langsung minta, semoga harganya masuk akal", likes: 76 },
  ],
  "Made with recycled tarpaulin": [
    { emotion: "Joy", author: "@kreative.id", text: "wait ini dari bahan terpal? keren juga", likes: 96 },
    { emotion: "Skeptical", author: "@harga.jujur", text: "recycled material tapi harga premium, klasik", likes: 74 },
    { emotion: "Joy", author: "@desainlokal", text: "gak nyangka terpal bisa jadi sepatu", likes: 51 },
    { emotion: "Neutral", author: "@nontonaja", text: "is the tarpaulin thing in the film? i missed it", likes: 12 },
  ],
  "Nostalgia for street football": [
    { emotion: "Joy", author: "@genz_bola", text: "jadi kangen main bola pakai sandal jepit", likes: 1410 },
    { emotion: "Joy", author: "@kampungfc", text: "gawangnya batu, garisnya pakai kapur, mantap", likes: 890 },
    { emotion: "Joy", author: "@melbournefc", text: "this is my childhood exactly, no notes", likes: 655 },
    { emotion: "Neutral", author: "@sedih_dikit", text: "sekarang lapangannya udah jadi ruko 😔", likes: 402 },
    { emotion: "Skeptical", author: "@tua_nonton", text: "nostalgia mulu, giliran liga kampung gak ditonton", likes: 143 },
  ],
  "Price and value": [
    { emotion: "Skeptical", author: "@dompettipis", text: "niatnya bagus, dompetnya yang gak sanggup", likes: 733 },
    { emotion: "Neutral", author: "@bandingin", text: "mending nabung 3 bulan atau beli yang lokal?", likes: 315 },
    { emotion: "Skeptical", author: "@reviewjujur", text: "di market place pasti banyak KW seminggu lagi", likes: 204 },
    { emotion: "Joy", author: "@firstpair", text: "sepatu bola pertamaku dulu juga yang penting bisa main", likes: 158 },
  ],
  "Will they survive concrete?": [
    { emotion: "Skeptical", author: "@betonwarior", text: "3 bulan mentok di lapangan semen, catat", likes: 521 },
    { emotion: "Neutral", author: "@tanyadulu", text: "ada review abis dipakai semusim gak?", likes: 246 },
    { emotion: "Skeptical", author: "@jebolterus", text: "jahitan depan pasti jebol duluan, selalu begitu", likes: 187 },
    { emotion: "Joy", author: "@optimis_fc", text: "kalau bener tahan beton ini game changer sih", likes: 95 },
  ],
  "Casting and cameos": [
    { emotion: "Joy", author: "@tangerangpride", text: "yang jadi kiper itu anak Tangerang kan? kenal banget mukanya", likes: 512 },
    { emotion: "Joy", author: "@realpeople", text: "casting-nya real people, bukan model, respect", likes: 388 },
    { emotion: "Joy", author: "@egyfans", text: "kirain bakal ada Egy, ternyata anak kampung semua, lebih bagus", likes: 244 },
    { emotion: "Neutral", author: "@scout_amatir", text: "who is the kid in the yellow jersey, he carried the ad", likes: 130 },
  ],
  "Craft and the music": [
    { emotion: "Joy", author: "@playlistgue", text: "backsound-nya nagih, ada di spotify gak?", likes: 622 },
    { emotion: "Joy", author: "@editorjakarta", text: "editingnya rapi banget buat iklan lokal", likes: 340 },
    { emotion: "Joy", author: "@dronestuff", text: "the drone shot over the gang, chef's kiss", likes: 201 },
    { emotion: "Neutral", author: "@audio_phile", text: "sound design-nya bikin merinding", likes: 88 },
  ],
  "Doubt about the brand's motive": [
    { emotion: "Skeptical", author: "@klapsindiran", text: "peduli kampung tapi jual sepatu 2 juta, oke deh 👏", likes: 1102 },
    { emotion: "Skeptical", author: "@kritisdulu", text: "brand baru sadar ada lapangan kampung setelah 30 tahun", likes: 540 },
    { emotion: "Skeptical", author: "@csr_watch", text: "nice ad, but the CSR budget would build an actual pitch", likes: 318 },
    { emotion: "Neutral", author: "@netralaja", text: "cuma marketing, tapi gue tetep suka filmnya", likes: 145 },
  ],
};

const FIXTURE_INTERPRETATION = [
  "The film's cultural claim landed almost intact. Nearly half of the conversation repeated the idea that the neighbourhood pitch is the real stadium, often unprompted and in the audience's own words — the strongest transfer we have measured in this demo.",
  "The product did not travel with it. Only 14% engaged the concrete-boot claim, and where they did, the tone flipped to a question: how long will these last on a real street pitch? Price talk took 18% of the conversation without the brief inviting it, which suggests the \"for everyone\" line was heard as a promise about cost, not access.",
];
const FIXTURE_QUOTE = { text: "lapangan depan rumah gue lebih rame dari GBK kalau sore 😂", attr: "Nostalgia · joy · 1,204 likes" };
const FIXTURE_CAVEAT = "Sarcasm is the label to trust least — Indonesian comment humour reads as praise to the model more often than we would like. Treat the \"joy\" share on the doubt theme as a floor, not a ceiling. All figures in this local demo are fixture data, not a real analysis.";

/* ============================== Run engine ============================== */

const STAGE_MESSAGES = {
  connecting: "Connecting to YouTube",
  collect: "Collecting the comments",
  brief: "Reading the brief and transcripts",
  brief_pause: "Waiting for you to confirm the ideas",
  classify: "Labelling every comment",
  emotion: "Double-checking the leftovers",
  report: "Writing your note",
  complete: "Complete",
  failed: "Failed",
};

const runEngines = new Map(); // runId -> engine

function makeRunEngine(run) {
  const engine = {
    run,
    subs: new Set(),
    timers: [],
    cancelled: false,
    disconnected: false,
    failArmed: false,
    proceedResolve: null,
    proceedPromise: null,
    counts: { total: 0, labelled: 0, themes: 7, other: 9, videos: 0 },
  };
  engine.proceedPromise = new Promise((res) => { engine.proceedResolve = res; });

  engine.after = (ms, fn) => {
    const t = setTimeout(() => {
      engine.timers = engine.timers.filter((x) => x !== t);
      if (!engine.cancelled) fn();
    }, ms);
    engine.timers.push(t);
    return t;
  };
  engine.every = (ms, fn) => {
    const t = setInterval(() => { if (!engine.cancelled) fn(); }, ms);
    engine.timers.push(t);
    return t;
  };
  engine.clearTimers = () => {
    for (const t of engine.timers) { clearTimeout(t); clearInterval(t); }
    engine.timers = [];
  };
  engine.emit = (stage, message, pct, detail) => {
    if (engine.cancelled) return;
    run.stage = stage; run.message = message; run.pct = pct;
    const event = { run_id: run.id, stage, message, pct, detail: detail || {} };
    for (const s of engine.subs) s.onEvent(event);
  };
  engine.destroy = () => {
    engine.cancelled = true;
    engine.clearTimers();
    engine.subs.clear();
  };
  engine.disconnectNow = () => {
    if (engine.disconnected) return;
    engine.disconnected = true;
    engine.clearTimers();
    for (const s of engine.subs) s.onDisconnect();
    // The analysis "keeps running" server-side; the stream resumes from the
    // same stage/pct after the caller's bounded backoff reconnect delay.
    engine.after(4000, () => {
      for (const s of engine.subs) s.onReconnect({ ...run });
      engine.disconnected = false;
      if (run.stage !== "brief_pause" && engine.go) engine.go(run.stage);
    });
  };
  engine.failNow = () => {
    if (run.stage === "brief_pause") { engine.failArmed = true; return; }
    failRun(engine, "Demo failure: the collection quota ran out mid-run.");
  };

  return engine;
}

function failRun(engine, message) {
  const run = engine.run;
  engine.clearTimers();
  run.status = "failed";
  run.error = message;
  touchSession(run.sessionId);
  const s = store.sessions.get(run.sessionId);
  if (s) s.status = "failed";
  engine.emit("failed", message, run.pct, { error: message });
  engine.after(60000, () => engine.destroy());
}

function finalizeRun(engine) {
  const run = engine.run;
  const points = run.briefPointIds
    .map((id) => store.briefPoints.get(id))
    .filter(Boolean)
    .sort((a, b) => a.order - b.order);
  const included = points.filter((p) => p.included);
  const total = engine.counts.total || 8412;

  // Transfer metrics come from the user's confirmed brief points. Values are
  // demo fixtures; labels follow whatever the user kept.
  const transfers = included.map((p, i) => {
    const fx = FIXTURE_TRANSFERS[i % FIXTURE_TRANSFERS.length];
    const known = FIXTURE_EVIDENCE[p.label] ? p.label : fx.label;
    return {
      id: "m-t-" + p.id,
      label: p.label,
      value: Math.max(2, fx.value - i),
      evidenceCount: 0,
      _evidenceKey: known,
    };
  });
  const overall = transfers.length
    ? Math.round(transfers.reduce((a, m) => a + m.value, 0) / transfers.length) + 12
    : 0;
  const themes = FIXTURE_THEMES.map((t, i) => ({
    id: "m-th-" + i,
    label: t.label,
    value: t.value,
    evidenceCount: 0,
    _evidenceKey: t.label,
  }));

  const evidence = [];
  const attach = (metric) => {
    const list = FIXTURE_EVIDENCE[metric._evidenceKey] || [];
    metric.evidenceCount = Math.round((metric.value / 100) * total);
    for (const e of list) {
      evidence.push({
        id: "ev-" + evidence.length,
        metricId: metric.id,
        emotion: e.emotion,
        author: e.author,
        text: e.text,
        likes: e.likes,
      });
    }
  };
  transfers.forEach(attach);
  themes.filter((t) => t.label !== "Other").forEach(attach);

  const top = transfers[0] || { label: "your brief", value: 0 };
  const report = {
    runId: run.id,
    title: "The street heard you.\nThe shoe did not.",
    subtitle: `${fmtNum(total)} comments · ${engine.counts.videos || 1} videos · ${themes.length - 1} themes · Indonesian / English`,
    overallTransfer: overall,
    transfers,
    themes,
    interpretation: FIXTURE_INTERPRETATION.join("\n\n").replace(
      "the neighbourhood pitch is the real stadium",
      `the idea that "${top.label.toLowerCase()}"`,
    ),
    quote: FIXTURE_QUOTE,
    caveat: FIXTURE_CAVEAT,
    evidence,
  };
  store.reports.set(run.id, report);

  const session = store.sessions.get(run.sessionId);
  const campaignId = session && session.campaignIds[0];
  const addedAt = nowIso();
  const mkArtifact = (name, kind, content) => {
    const a = {
      id: uid(), runId: run.id, campaignId, name, kind,
      size: new Blob([content]).size, addedAt, content,
    };
    store.artifacts.set(a.id, a);
    return a;
  };
  const csvLine = (cols) => cols.map((c) => {
    const s = String(c == null ? "" : c);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }).join(",");
  mkArtifact(
    "chart_transfer.csv", "csv",
    ["idea,share_pct,evidence_count"]
      .concat(transfers.map((m) => csvLine([m.label, m.value, m.evidenceCount])))
      .join("\n") + "\n",
  );
  mkArtifact(
    "chart_themes.csv", "csv",
    ["theme,share_pct"]
      .concat(themes.map((m) => csvLine([m.label, m.value])))
      .join("\n") + "\n",
  );
  mkArtifact(
    "comments.csv", "csv",
    ["author,emotion,theme_or_idea,text,likes"]
      .concat(evidence.map((e) => {
        const m = transfers.concat(themes).find((x) => x.id === e.metricId);
        return csvLine([e.author, e.emotion, m ? m.label : "", e.text, e.likes]);
      }))
      .join("\n") + "\n",
  );
  mkArtifact("report.pdf", "pdf", buildDemoPdf(report));

  if (session) {
    session.status = "complete";
    session.commentCount = total;
    session.updatedAt = addedAt;
  }
}

function buildDemoPdf(report) {
  // Minimal valid single-page PDF, clearly marked as demo data.
  const esc = (s) => String(s)
    .replace(/[^\x20-\x7E]/g, "?") // Helvetica is latin-1; fixture copy has emoji/dashes
    .replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  const lines = [
    [report.title.split("\n")[0], 24, 770],
    [report.title.split("\n")[1] || "", 24, 736],
    [report.subtitle, 10, 700],
    ["Overall signal transfer: " + report.overallTransfer + "%", 16, 660],
    ["Which ideas arrived:", 12, 620],
  ];
  for (const m of report.transfers) lines.push([m.label + " - " + m.value + "%", 10, null]);
  lines.push(["", 10, null]);
  lines.push(["LOCAL DEMO FIXTURE DATA - not a real analysis.", 10, null]);
  let y = 0;
  const ops = ["BT"];
  for (const [text, size, absY] of lines) {
    if (absY != null) y = absY; else y -= size + 8;
    ops.push(`/F1 ${size} Tf 1 0 0 1 56 ${y} Tm (${esc(text)}) Tj`);
  }
  ops.push("ET");
  const stream = ops.join("\n");
  const objects = [];
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = "<< /Type /Pages /Kids [3 0 R] /Count 1 >>";
  objects[3] = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>";
  objects[4] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";
  objects[5] = "<< /Length " + stream.length + " >>\nstream\n" + stream + "\nendstream";
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (let i = 1; i <= 5; i++) {
    offsets[i] = pdf.length;
    pdf += i + " 0 obj\n" + objects[i] + "\nendobj\n";
  }
  const xref = pdf.length;
  pdf += "xref\n0 6\n0000000000 65535 f \n";
  for (let i = 1; i <= 5; i++) {
    pdf += String(offsets[i]).padStart(10, "0") + " 00000 n \n";
  }
  pdf += "trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + xref + "\n%%EOF";
  return pdf;
}

/* The stage machine lives in makeRunEngine/startEngineSchedule — all fixture
   timing stays in this layer, never in renderers. */

function startEngineSchedule(engine) {
  const run = engine.run;
  const session = store.sessions.get(run.sessionId);
  const campaign = session && store.campaigns.get(session.campaignIds[0]);
  const videos = campaign ? campaign.videoIds.map((id) => store.videos.get(id)).filter(Boolean) : [];
  engine.counts.total = Math.max(1200, videos.reduce((a, v) => a + v.commentCount, 0) || 8412);
  engine.counts.videos = videos.length || 1;

  const go = (stage) => {
    if (engine.cancelled || engine.disconnected) return;
    if (stage === "connecting") {
      engine.emit("connecting", STAGE_MESSAGES.connecting, 2, {});
      engine.after(1200, () => go("collect"));
    } else if (stage === "collect") {
      engine.emit("collect", STAGE_MESSAGES.collect, 6, { collected: 0, total: engine.counts.total });
      let collected = 0;
      const step = Math.ceil(engine.counts.total / 6);
      engine.every(400, () => {
        collected = Math.min(engine.counts.total, collected + step);
        engine.emit("collect", STAGE_MESSAGES.collect, 6 + Math.round(14 * collected / engine.counts.total),
          { collected, total: engine.counts.total, videos: engine.counts.videos });
        if (collected >= engine.counts.total) {
          engine.clearTimers();
          engine.after(500, () => go("brief"));
        }
      });
    } else if (stage === "brief") {
      engine.emit("brief", STAGE_MESSAGES.brief, 24, {});
      engine.after(2400, () => go("brief_pause"));
    } else if (stage === "brief_pause") {
      run.status = "running";
      engine.emit("brief_pause", STAGE_MESSAGES.brief_pause, 30, {});
      engine.proceedPromise.then(() => {
        if (engine.cancelled || engine.disconnected) return;
        // Re-enter through the outer guard so a disconnect during classify
        // resumes at classify rather than jumping backwards.
        go("classify");
      });
    } else if (stage === "classify") {
      engine.emit("classify", STAGE_MESSAGES.classify, 34, { labelled: 0, total: engine.counts.total });
      let labelled = 0;
      const total = engine.counts.total;
      const step = Math.ceil(total / 22);
      engine.every(300, () => {
        labelled = Math.min(total, labelled + step);
        const frac = labelled / total;
        engine.emit("classify", STAGE_MESSAGES.classify, 34 + Math.round(44 * frac), {
          labelled, total, batch: Math.ceil(labelled / 25), batches: Math.ceil(total / 25),
          other: Math.max(4, Math.round(12 - 8 * frac)),
        });
        if (labelled >= total) {
          engine.clearTimers();
          engine.counts.labelled = total;
          engine.after(400, () => go("emotion"));
        }
      });
    } else if (stage === "emotion") {
      engine.emit("emotion", STAGE_MESSAGES.emotion, 82, {});
      engine.after(2200, () => go("report"));
    } else if (stage === "report") {
      engine.emit("report", STAGE_MESSAGES.report, 92, {});
      engine.after(2200, () => {
        finalizeRun(engine);
        run.status = "complete";
        touchSession(run.sessionId);
        engine.emit("complete", STAGE_MESSAGES.complete, 100, {});
        engine.after(60000, () => engine.destroy());
      });
    }
  };

  engine.go = go;
  go(run.stage === "queued" ? "connecting" : run.stage);
}

/* ============================== demoApi ============================== */

const demoApi = {
  /** @returns {Promise<Array<object>>} all sessions, newest first */
  async listSessions() {
    return [...store.sessions.values()]
      .map((s) => ({ ...s, campaignIds: [...s.campaignIds] }))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  },

  /** @param {string} id @returns {Promise<object>} */
  async getSession(id) {
    const s = store.sessions.get(id);
    if (!s) throw demoError("not_found", "Session not found.");
    return { ...s, campaignIds: [...s.campaignIds] };
  },

  /**
   * @param {{ name: string, campaignName: string, videoUrls: string[] }} input
   * @returns {Promise<{ session: object, campaign: object }>}
   */
  async createSession(input) {
    const name = requireName(input.name, "Session name");
    const campaignName = requireName(input.campaignName, "Campaign name");
    const urls = input.videoUrls || [];
    if (!urls.length) throw demoError("validation", "Add at least one YouTube link.", "videos");
    const seen = new Set();
    const parsed = urls.map((u) => {
      const p = parseYouTubeUrl(u);
      if (p.error) throw demoError("validation", p.error, "videos");
      if (seen.has(p.videoId)) throw demoError("validation", "That video is already in the list.", "videos");
      seen.add(p.videoId);
      return { url: u.trim(), ...p };
    });

    const ts = nowIso();
    const session = {
      id: uid(), name, campaignIds: [], status: "ready",
      commentCount: 0, updatedAt: ts,
    };
    const campaign = {
      id: uid(), sessionId: session.id, name: campaignName,
      videoIds: [], assetIds: [], briefPointIds: [],
    };
    session.campaignIds.push(campaign.id);
    store.sessions.set(session.id, session);
    store.campaigns.set(campaign.id, campaign);
    for (const p of parsed) {
      const meta = demoVideoMeta(p.videoId, p.kind);
      const v = {
        id: uid(), campaignId: campaign.id, url: p.url, videoId: p.videoId,
        kind: p.kind, title: meta.title, channel: meta.channel,
        thumbnailUrl: null, commentCount: meta.commentCount,
      };
      store.videos.set(v.id, v);
      campaign.videoIds.push(v.id);
    }
    return { session: { ...session }, campaign: { ...campaign } };
  },

  /** @param {string} id @returns {Promise<object>} */
  async getCampaign(id) {
    const c = store.campaigns.get(id);
    if (!c) throw demoError("not_found", "Campaign not found.");
    return {
      ...c,
      videoIds: [...c.videoIds], assetIds: [...c.assetIds], briefPointIds: [...c.briefPointIds],
      videos: c.videoIds.map((v) => ({ ...store.videos.get(v) })),
      assets: c.assetIds.map((a) => ({ ...store.assets.get(a) })),
    };
  },

  /**
   * @param {string} campaignId @param {string} url
   * @returns {Promise<object>} the new Video
   */
  async addVideo(campaignId, url) {
    const c = store.campaigns.get(campaignId);
    if (!c) throw demoError("not_found", "Campaign not found.");
    const p = parseYouTubeUrl(url);
    if (p.error) throw demoError("validation", p.error, "url");
    const dupe = c.videoIds.map((id) => store.videos.get(id)).find((v) => v.videoId === p.videoId);
    if (dupe) throw demoError("validation", "That video is already in this campaign.", "url");
    const meta = demoVideoMeta(p.videoId, p.kind);
    const v = {
      id: uid(), campaignId, url: url.trim(), videoId: p.videoId, kind: p.kind,
      title: meta.title, channel: meta.channel, thumbnailUrl: null,
      commentCount: meta.commentCount,
    };
    store.videos.set(v.id, v);
    c.videoIds.push(v.id);
    touchSession(c.sessionId);
    return { ...v };
  },

  /** @param {string} videoId @returns {Promise<void>} */
  async removeVideo(videoId) {
    const v = store.videos.get(videoId);
    if (!v) return;
    const c = store.campaigns.get(v.campaignId);
    if (c) c.videoIds = c.videoIds.filter((id) => id !== videoId);
    store.videos.delete(videoId);
    if (c) touchSession(c.sessionId);
  },

  /**
   * Validates before mutating.
   * @param {string} campaignId @param {File} file
   * @returns {Promise<object>} the new Asset
   */
  async uploadAsset(campaignId, file) {
    const c = store.campaigns.get(campaignId);
    if (!c) throw demoError("not_found", "Campaign not found.");
    const name = (file.name || "").trim();
    if (!name) throw demoError("validation", "The file needs a name.", "file");
    const ext = (name.split(".").pop() || "").toLowerCase();
    if (!UPLOAD_EXTS.includes(ext)) {
      throw demoError("validation", "PDF, PPTX, DOCX, PNG, JPG or WEBP only.", "file");
    }
    if (file.size > MAX_UPLOAD) {
      throw demoError("validation", "Files are limited to 10 MB.", "file");
    }
    const isImage = IMAGE_EXTS.includes(ext) || IMAGE_MIMES.includes(file.type);
    const asset = {
      id: uid(), campaignId,
      kind: isImage ? "image" : "document",
      name: file.name, sourceUrl: null,
      mimeType: file.type || "application/octet-stream",
      size: file.size, addedAt: nowIso(), isKeyVisual: false, status: "ready",
    };
    store.assets.set(asset.id, asset);
    store.assetData.set(asset.id, file);
    c.assetIds.push(asset.id);
    touchSession(c.sessionId);
    return { ...asset };
  },

  /**
   * @param {string} campaignId @param {string} url
   * @returns {Promise<object>} the new Asset (kind "article")
   */
  async addArticle(campaignId, url) {
    const c = store.campaigns.get(campaignId);
    if (!c) throw demoError("not_found", "Campaign not found.");
    const u = (url || "").trim();
    if (!/^https?:\/\/.+/i.test(u)) {
      throw demoError("validation", "Articles need a full http:// or https:// link.", "article");
    }
    let host = "";
    try { host = new URL(u).hostname; } catch { throw demoError("validation", "That link does not look right.", "article"); }
    const asset = {
      id: uid(), campaignId, kind: "article",
      name: host + " — article", sourceUrl: u,
      mimeType: "text/html", size: null, addedAt: nowIso(),
      isKeyVisual: false, status: "ready",
    };
    store.assets.set(asset.id, asset);
    c.assetIds.push(asset.id);
    touchSession(c.sessionId);
    return { ...asset };
  },

  /** @param {string} assetId @returns {Promise<void>} */
  async removeAsset(assetId) {
    const a = store.assets.get(assetId);
    if (!a) return;
    const c = store.campaigns.get(a.campaignId);
    if (c) c.assetIds = c.assetIds.filter((id) => id !== assetId);
    store.assets.delete(assetId);
    store.assetData.delete(assetId);
    if (c) touchSession(c.sessionId);
  },

  /**
   * Future backend contract: PATCH /api/assets/{id} with body { "is_key_visual": true }.
   * @param {string} campaignId @param {string} assetId
   * @returns {Promise<object>} the updated Asset
   */
  async setKeyVisual(campaignId, assetId) {
    const c = store.campaigns.get(campaignId);
    if (!c) throw demoError("not_found", "Campaign not found.");
    const a = store.assets.get(assetId);
    if (!a || a.campaignId !== campaignId) throw demoError("not_found", "Asset not found.");
    if (a.kind !== "image") throw demoError("validation", "Only images can be the key visual.", "asset");
    for (const id of c.assetIds) {
      const other = store.assets.get(id);
      if (other) other.isKeyVisual = id === assetId;
    }
    touchSession(c.sessionId);
    return { ...a };
  },

  /**
   * Always creates a fresh run ID, even for a session that already ran.
   * @param {string} sessionId @returns {Promise<object>} the new Run
   */
  async startRun(sessionId) {
    const s = store.sessions.get(sessionId);
    if (!s) throw demoError("not_found", "Session not found.");
    for (const r of store.runs.values()) {
      if ((r.status === "queued" || r.status === "running")) {
        const eng = runEngines.get(r.id);
        if (eng && !eng.cancelled) {
          throw demoError("conflict", "A run is already in progress. Wait for it to finish.");
        }
      }
    }
    const run = {
      id: uid(), sessionId, status: "queued", stage: "connecting",
      pct: 0, message: "Queued", briefPointIds: [], error: null, createdAt: nowIso(),
    };
    // Fresh brief points per run, seeded from the campaign's assets/brief.
    const campaign = store.campaigns.get(s.campaignIds[0]);
    const pointCount = campaign && campaign.assetIds.length ? 5 : 4;
    for (let i = 0; i < pointCount; i++) {
      const fx = FIXTURE_POINTS[i];
      const p = {
        id: uid(), runId: run.id, label: fx.label, description: fx.description,
        included: true, order: i + 1,
      };
      store.briefPoints.set(p.id, p);
      run.briefPointIds.push(p.id);
    }
    store.runs.set(run.id, run);
    s.status = "running";
    s.updatedAt = nowIso();
    const engine = makeRunEngine(run);
    runEngines.set(run.id, engine);
    startEngineSchedule(engine);
    return { ...run };
  },

  /** @param {string} id @returns {Promise<object>} */
  async getRun(id) {
    const r = store.runs.get(id);
    if (!r) throw demoError("not_found", "Run not found.");
    const eng = runEngines.get(id);
    return {
      ...r, briefPointIds: [...r.briefPointIds],
      counts: eng ? { ...eng.counts } : null,
      disconnected: eng ? eng.disconnected : false,
    };
  },

  /**
   * SSE-shaped subscription. Returns an unsubscribe function that detaches all
   * listeners for this subscription; engine timers are shared per run and
   * cleaned up when the run engine is destroyed.
   * @param {string} id
   * @param {{ onEvent: (e: object) => void, onDisconnect: () => void, onReconnect: (run: object) => void }} handlers
   * @returns {() => void} unsubscribe
   */
  subscribeRun(id, handlers) {
    const eng = runEngines.get(id);
    if (!eng) {
      // Run already finished and engine reaped: replay terminal state once.
      const r = store.runs.get(id);
      if (r) {
        setTimeout(() => handlers.onEvent({
          run_id: r.id, stage: r.stage, message: r.message, pct: r.pct, detail: {},
        }), 0);
      }
      return () => {};
    }
    const sub = {
      onEvent: handlers.onEvent, onDisconnect: handlers.onDisconnect,
      onReconnect: handlers.onReconnect,
    };
    eng.subs.add(sub);
    // Late joiners get current state immediately.
    setTimeout(() => {
      if (!eng.subs.has(sub)) return; // unsubscribed before the replay landed
      sub.onEvent({ run_id: id, stage: eng.run.stage, message: eng.run.message, pct: eng.run.pct, detail: {} });
      if (eng.disconnected) sub.onDisconnect();
    }, 0);
    return () => { eng.subs.delete(sub); };
  },

  /**
   * @param {string} runId
   * @param {Array<{ id: string, label: string, description: string, included: boolean, order: number }>} points
   * @returns {Promise<Array<object>>} saved points in order
   */
  async updateBriefPoints(runId, points) {
    const run = store.runs.get(runId);
    if (!run) throw demoError("not_found", "Run not found.");
    if (run.stage !== "brief_pause") {
      throw demoError("conflict", "Brief review is only open while the run is paused.");
    }
    const kept = [];
    for (const p of points) {
      const label = (p.label || "").trim();
      if (!label) throw demoError("validation", "Every idea needs a label.", "label");
      kept.push({
        id: p.id, runId, label,
        description: (p.description || "").trim(),
        included: !!p.included, order: p.order,
      });
    }
    if (!kept.some((p) => p.included)) {
      throw demoError("validation", "Keep at least one idea included.", "points");
    }
    for (const oldId of run.briefPointIds) store.briefPoints.delete(oldId);
    run.briefPointIds = kept.map((p) => p.id);
    for (const p of kept) store.briefPoints.set(p.id, p);
    return kept.map((p) => ({ ...p }));
  },

  /** @param {string} runId @returns {Promise<object>} */
  async proceedRun(runId) {
    const run = store.runs.get(runId);
    if (!run) throw demoError("not_found", "Run not found.");
    if (run.stage !== "brief_pause") {
      throw demoError("conflict", "This run is not waiting for review.");
    }
    const points = run.briefPointIds.map((id) => store.briefPoints.get(id)).filter(Boolean);
    if (!points.some((p) => p.included)) {
      throw demoError("validation", "Keep at least one idea included before continuing.", "points");
    }
    const eng = runEngines.get(runId);
    if (eng && eng.proceedResolve) {
      const res = eng.proceedResolve;
      eng.proceedResolve = null;
      res();
      if (eng.failArmed) {
        eng.failArmed = false;
        engineFailAfterProceed(eng);
      }
    }
    return { ...run };
  },

  /** Demo-only hook behind the Demo controls disclosure. */
  async simulateDisconnect(runId) {
    const eng = runEngines.get(runId);
    if (!eng || eng.disconnected) return;
    if (eng.run.stage === "complete" || eng.run.stage === "failed") return;
    eng.disconnectNow();
  },

  /** Demo-only hook behind the Demo controls disclosure. */
  async simulateFailure(runId) {
    const eng = runEngines.get(runId);
    if (!eng) return;
    if (eng.run.stage === "complete" || eng.run.stage === "failed") return;
    eng.failNow();
  },

  /** @param {string} runId @returns {Promise<object>} the Report */
  async getReport(runId) {
    const report = store.reports.get(runId);
    if (!report) throw demoError("conflict", "The report is only available once the run completes.");
    return { ...report };
  },

  /**
   * Uploaded file contents for preview/download. Null when absent (articles
   * carry no blob).
   * @param {string} assetId @returns {Promise<Blob|File|null>}
   */
  async getAssetData(assetId) {
    return store.assetData.get(assetId) || null;
  },

  /** @param {string} sessionId @returns {Promise<object|null>} the active run, if any */
  async getRunningRun(sessionId) {
    for (const r of store.runs.values()) {
      if (r.sessionId === sessionId && (r.status === "queued" || r.status === "running")) {
        return { ...r, briefPointIds: [...r.briefPointIds] };
      }
    }
    return null;
  },

  /**
   * @param {string} runId @returns {Promise<Array<object>>} artifacts produced by the run
   */
  async listArtifacts(runId) {
    return [...store.artifacts.values()]
      .filter((a) => a.runId === runId)
      .map((a) => ({ ...a }));
  },

  /** @returns {Promise<Array<object>>} mixed Assets and Artifacts */
  async listFiles() {
    const files = [];
    for (const a of store.assets.values()) {
      files.push({ ...a, _file: "asset" });
    }
    for (const a of store.artifacts.values()) {
      files.push({ ...a, _file: "artifact" });
    }
    files.sort((a, b) => b.addedAt.localeCompare(a.addedAt));
    return files;
  },

  /** @param {string} id @returns {Promise<object>} */
  async getArtifact(id) {
    const a = store.artifacts.get(id);
    if (!a) throw demoError("not_found", "File not found.");
    return { ...a };
  },
};

function engineFailAfterProceed(eng) {
  const t = setTimeout(() => {
    if (!eng.cancelled) failRun(eng, "Demo failure: the classification model stopped responding.");
  }, 1500);
  eng.timers.push(t);
}

window.demoApi = demoApi;

/* ============================== View helpers ============================== */

const view = document.getElementById("view");
const topbar = document.getElementById("topbar");
const overlayRoot = document.getElementById("overlay-root");

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fmtNum(n) {
  return (n || 0).toLocaleString("en-US");
}

function fmtSize(bytes) {
  if (bytes == null) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function fmtAgo(iso) {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return m + " min ago";
  const h = Math.floor(m / 60);
  if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
  const d = Math.floor(h / 24);
  return d + (d === 1 ? " day ago" : " days ago");
}

const ICONS = {
  x: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
  xLg: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
  plus: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg>',
  plusSm: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"></path></svg>',
  chevR: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M9 5l7 7-7 7"></path></svg>',
  chevL: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 5l-7 7 7 7"></path></svg>',
  up: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4"></path><path d="M7.5 8.5 12 4l4.5 4.5"></path><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"></path></svg>',
  yt: '<svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor"><path d="M21.6 7.2a2.8 2.8 0 0 0-2-2C17.9 4.8 12 4.8 12 4.8s-5.9 0-7.6.4a2.8 2.8 0 0 0-2 2C2 8.9 2 12 2 12s0 3.1.4 4.8a2.8 2.8 0 0 0 2 2c1.7.4 7.6.4 7.6.4s5.9 0 7.6-.4a2.8 2.8 0 0 0 2-2C22 15.1 22 12 22 12s0-3.1-.4-4.8zM10 15.2V8.8l5.2 3.2z"></path></svg>',
  star: '<svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.4l2 5.7 5.7 2-5.7 2-2 5.7-2-5.7-5.7-2 5.7-2z"></path></svg>',
  starLg: '<svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.4l2 5.7 5.7 2-5.7 2-2 5.7-2-5.7-5.7-2 5.7-2z"></path></svg>',
  folder: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6.5h6l2 2.2h10V19a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"></path></svg>',
  link: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 0 0-5.7-5.7L11 6.2"></path><path d="M14 11a4 4 0 0 0-5.7 0L6 13.3a4 4 0 0 0 5.7 5.7l1.3-1.2"></path></svg>',
  search: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><line x1="21" y1="21" x2="16.2" y2="16.2"></line></svg>',
  check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M5 12.5l4.5 4.5L19 7"></path></svg>',
  upArr: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 19V5M5 12l7-7 7 7"></path></svg>',
  downArr: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12l7 7 7-7"></path></svg>',
  play: '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l11-6.5z"></path></svg>',
  send: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l16-8-6 8 6 8z"></path></svg>',
};

/** Disabled control with an explanation on hover/focus. */
function disWrap(innerHtml, reason) {
  return `<span class="dis-wrap" tabindex="0" aria-label="${esc(reason)}">${innerHtml}<span class="dis-tip" role="tooltip">${esc(reason)}</span></span>`;
}

function downloadBlob(content, name, mime) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/* Modal with focus trap, Escape, and focus restoration. */
let modalState = null;
function openModal(title, bodyHtml, objectUrl) {
  closeModal();
  const prevFocus = document.activeElement;
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <div class="modal-head">
        <div class="t">${esc(title)}</div>
        <button class="ev-close" data-close aria-label="Close preview">${ICONS.xLg}</button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
    </div>`;
  overlayRoot.appendChild(backdrop);
  const closeBtn = backdrop.querySelector("[data-close]");
  modalState = { el: backdrop, prevFocus, url: objectUrl || null };
  closeBtn.focus();
  closeBtn.addEventListener("click", closeModal);
  backdrop.addEventListener("mousedown", (e) => { if (e.target === backdrop) closeModal(); });
  backdrop.addEventListener("keydown", (e) => {
    if (e.key === "Tab") {
      const f = backdrop.querySelectorAll("button, [href], iframe, [tabindex]:not([tabindex='-1'])");
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
}
function closeModal() {
  if (!modalState) return;
  if (modalState.url) URL.revokeObjectURL(modalState.url);
  modalState.el.remove();
  if (modalState.prevFocus && modalState.prevFocus.focus) modalState.prevFocus.focus();
  modalState = null;
}

function setTopbar(html) { topbar.innerHTML = html; }

function setSidebarActive(which) {
  for (const id of ["sb-sessions", "sb-files"]) {
    document.getElementById(id).classList.toggle("active", id === "sb-" + which);
  }
}

/* ============================== Screens ============================== */

let routeCleanup = null;

function cleanupRoute() {
  if (routeCleanup) { routeCleanup(); routeCleanup = null; }
  closeModal();
  closeEvidenceDrawer();
}

/* ---------- Home ---------- */
async function renderHome() {
  setSidebarActive("sessions");
  const sessions = await demoApi.listSessions();
  setTopbar(`
    <div class="topbar-left"><span class="topbar-title">Sessions</span></div>
    <div class="topbar-right">
      <span class="topbar-org">Innocean Indonesia</span>
      <a class="btn primary" href="#/sessions/new">New session</a>
    </div>`);

  view.innerHTML = `
  <div class="view-pad" style="gap:30px">
    <section class="hero" aria-label="Assistant">
      <div class="hero-top">
        <div class="hero-star">${ICONS.starLg}</div>
        <div class="hero-body">
          <h1>Hi there — what conversation<br><span class="accent">should we listen to today?</span></h1>
          ${disWrap(`
            <div class="hero-prompt dis" aria-disabled="true">
              <div class="ph">Paste a YouTube link or a brief, or just describe the campaign you want to understand…</div>
              <div class="row">
                <div class="plusbox">${ICONS.plus}</div>
                <div class="send">${ICONS.send}</div>
              </div>
            </div>`,
            "The assistant is not available in this local demo. Create a session manually below.")}
          <div class="hero-note">Resonance only reports what real comments say — every number opens the comments behind it.</div>
        </div>
      </div>
      <div class="hero-foot">
        <span class="lead">This local demo runs without the assistant.</span>
        <span class="why">Start with a manual session — everything else works end to end on fixture data.</span>
      </div>
    </section>

    <section aria-label="Your sessions" style="display:flex;flex-direction:column;gap:14px">
      <div class="section-label">Your sessions</div>
      ${sessions.length === 0 ? `
      <div class="empty-block">
        <div class="empty-icon">${ICONS.folder}</div>
        <h3>No sessions yet</h3>
        <p>A session groups the campaigns you want to read together — Nike and Adidas in the same category, or one brand across three films.</p>
        <div class="actions">
          <a class="btn primary lg" href="#/sessions/new">Create your first session</a>
        </div>
      </div>` : `
      <div class="empty-block">
        <h3>${sessions.length} session${sessions.length === 1 ? "" : "s"} in this demo</h3>
        <p>Nothing is saved between page loads — this local demo keeps everything in memory.</p>
        <div class="actions">
          <a class="btn primary" href="#/sessions">Open sessions</a>
          <a class="btn secondary" href="#/sessions/new">New session</a>
        </div>
      </div>`}
    </section>

    <section class="steps" aria-label="How it works">
      <div class="step"><span class="k">STEP 1</span><span class="t">Add the campaign</span><span class="d">YouTube links, the brief PDF, key visuals, press articles.</span></div>
      <div class="step"><span class="k">STEP 2</span><span class="t">We read every comment</span><span class="d">Each one gets a theme label, so percentages are counted, not guessed.</span></div>
      <div class="step"><span class="k">STEP 3</span><span class="t">You get a strategy note</span><span class="d">Two charts, a written read, and the comments behind every figure.</span></div>
    </section>
  </div>`;
}

/* ---------- Sessions ---------- */
const STATUS_LABEL = { draft: "Draft", ready: "Ready", running: "Running", complete: "Complete", failed: "Failed" };

async function renderSessions() {
  setSidebarActive("sessions");
  const sessions = await demoApi.listSessions();
  setTopbar(`
    <div class="topbar-left"><span class="topbar-title">Sessions</span></div>
    <div class="topbar-right">
      ${disWrap(`<span class="searchbox" aria-disabled="true">${ICONS.search}<span>Search sessions</span></span>`,
        "Search is not available in this local demo.")}
      <a class="btn primary" href="#/sessions/new">New session</a>
    </div>`);

  if (!sessions.length) {
    view.innerHTML = `
    <div class="view-pad">
      <div>
        <h2 class="greeting">Sessions</h2>
        <div class="greeting-sub">Each session groups the campaigns you want to read together.</div>
      </div>
      <div class="empty-block">
        <div class="empty-icon">${ICONS.folder}</div>
        <h3>No sessions yet</h3>
        <p>Create a session, add YouTube links and the campaign brief, and run your first read.</p>
        <div class="actions"><a class="btn primary lg" href="#/sessions/new">Create your first session</a></div>
      </div>
    </div>`;
    return;
  }

  const totalComments = sessions.reduce((a, s) => a + s.commentCount, 0);
  const rows = await Promise.all(sessions.map(async (s) => {
    const campaigns = (await Promise.all(s.campaignIds.map((id) => demoApi.getCampaign(id))))
      .map((c, i) => ({ ...c, id: s.campaignIds[i] }));
    const tags = campaigns.slice(0, 2).map((c) => `<span class="campaign-tag">${esc(c.name)}</span>`).join("")
      + (campaigns.length > 2 ? `<span class="campaign-tag more">+${campaigns.length - 2} more</span>` : "");
    const target = campaigns.length
      ? `#/sessions/${s.id}/campaigns/${campaigns[0].id}`
      : "#/sessions";
    const runningRun = await demoApi.getRunningRun(s.id);
    const statusCell = s.status === "running" && runningRun
      ? `<div style="display:flex;flex-direction:column;gap:6px">
           <span class="status running"><span class="dot"></span>${esc(runningRun.message || "Running")}</span>
           <div class="progressbar" style="width:82px"><div style="width:${runningRun.pct}%"></div></div>
         </div>`
      : `<span class="status ${s.status}"><span class="dot"></span>${STATUS_LABEL[s.status]}</span>`;
    return `
      <a class="trow" style="grid-template-columns:1.5fr 1.4fr .7fr .8fr .7fr 20px" href="${target}">
        <div class="trow-name">${esc(s.name)}</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px">${tags || '<span class="trow-dim">No campaigns yet</span>'}</div>
        <div class="trow-num">${s.commentCount ? fmtNum(s.commentCount) : "—"}${campaigns.length ? `<div class="sub">${campaigns.reduce((a, c) => a + c.videoIds.length, 0)} videos</div>` : ""}</div>
        <div>${statusCell}</div>
        <div class="trow-dim">${fmtAgo(s.updatedAt)}</div>
        <div class="chev">${ICONS.chevR}</div>
      </a>`;
  }));

  view.innerHTML = `
  <div class="view-pad">
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div style="display:flex;flex-direction:column;gap:5px">
        <h2 class="greeting">Good afternoon</h2>
        <div class="greeting-sub">${sessions.length} session${sessions.length === 1 ? "" : "s"} · ${fmtNum(totalComments)} comments read in this demo</div>
      </div>
      <div class="pill-row" role="group" aria-label="Session filters">
        <button class="pill active" type="button">All</button>
        ${disWrap('<button class="pill" type="button" disabled>Drafts</button>', "Drafts are not available in this local demo — sessions save immediately.")}
      </div>
    </div>
    <div class="table-scroll">
    <div class="table" style="min-width:760px">
      <div class="thead" style="grid-template-columns:1.5fr 1.4fr .7fr .8fr .7fr 20px">
        <div>SESSION</div><div>CAMPAIGNS</div><div>COMMENTS</div><div>STATUS</div><div>UPDATED</div><div></div>
      </div>
      ${rows.join("")}
    </div>
    </div>
  </div>`;
}

/* ---------- New session ---------- */
async function renderNewSession() {
  setSidebarActive("sessions");
  setTopbar(`
    <div class="topbar-left">
      <a class="crumb-back" href="#/sessions">
        ${ICONS.chevL}<span>Back to sessions</span>
      </a>
    </div>
    <div class="topbar-right"></div>`);

  view.innerHTML = `
  <div class="setup-wrap">
    <form class="setup" id="setup-form" novalidate>
      <div style="display:flex;flex-direction:column;gap:9px">
        <h1>Set up your session</h1>
        <div class="lede">Two names and at least one YouTube link. You can add briefs and assets on the next screen.</div>
      </div>

      <div class="field">
        <label for="f-session-name">Session name</label>
        <input class="input" id="f-session-name" name="sessionName" type="text" autocomplete="off" placeholder="Ramadan football push">
        <div class="hint">Sessions are for organising — group the campaigns you want to read together.</div>
        <div class="field-error" id="err-session" hidden></div>
      </div>

      <div class="field">
        <label for="f-campaign-name">First campaign</label>
        <input class="input" id="f-campaign-name" name="campaignName" type="text" autocomplete="off" placeholder="Nike — Bola Rakyat">
        <div class="hint">One brand, one film — or a set of films for the same idea.</div>
        <div class="field-error" id="err-campaign" hidden></div>
      </div>

      <div class="field">
        <span class="label" id="urls-label">YouTube links</span>
        <div class="hint">Add links one at a time: youtube.com/watch?v=, youtu.be/ or youtube.com/shorts/. No playlists.</div>
        <div class="url-list" id="url-list" role="list" aria-labelledby="urls-label"></div>
        <div class="url-add">
          <input class="input" id="f-url" type="url" autocomplete="off" placeholder="Paste a YouTube link" aria-labelledby="urls-label">
          <button class="btn secondary" type="button" id="btn-add-url">Add link</button>
        </div>
        <div class="field-error" id="err-url" hidden></div>
        <div class="field-error" id="err-videos" hidden></div>
      </div>

      <div class="setup-foot">
        <button class="btn primary lg" type="submit">Create session</button>
        ${disWrap('<button class="btn lg" type="button" disabled>Save as draft</button>', "Drafts are not available in this local demo — sessions save immediately.")}
        <span class="tail"></span>
        <span class="note">Brief and assets come next</span>
      </div>
    </form>
  </div>`;

  const added = [];
  const listEl = document.getElementById("url-list");
  const urlInput = document.getElementById("f-url");
  const errUrl = document.getElementById("err-url");
  const errVideos = document.getElementById("err-videos");

  function showErr(el, msg) {
    if (msg) { el.textContent = msg; el.hidden = false; } else { el.hidden = true; }
  }
  function paintUrls() {
    listEl.innerHTML = added.map((u, i) => `
      <div class="url-row" role="listitem">
        <span class="yt">${ICONS.yt}</span>
        <span class="u">${esc(u)}</span>
        <button class="icon-btn" type="button" data-rm="${i}" aria-label="Remove ${esc(u)}">${ICONS.x}</button>
      </div>`).join("");
    listEl.querySelectorAll("[data-rm]").forEach((b) => {
      b.addEventListener("click", () => { added.splice(Number(b.dataset.rm), 1); paintUrls(); });
    });
  }
  function addUrl() {
    const val = urlInput.value;
    const p = parseYouTubeUrl(val);
    if (p.error) { showErr(errUrl, p.error); urlInput.classList.add("error"); return; }
    const dup = added.some((u) => parseYouTubeUrl(u).videoId === p.videoId);
    if (dup) { showErr(errUrl, "That video is already in the list."); urlInput.classList.add("error"); return; }
    showErr(errUrl, null); showErr(errVideos, null);
    urlInput.classList.remove("error");
    added.push(val.trim());
    urlInput.value = "";
    paintUrls();
    urlInput.focus();
  }
  document.getElementById("btn-add-url").addEventListener("click", addUrl);
  urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addUrl(); }
  });

  document.getElementById("setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("f-session-name");
    const camp = document.getElementById("f-campaign-name");
    const errS = document.getElementById("err-session");
    const errC = document.getElementById("err-campaign");
    let ok = true;
    if (!name.value.trim()) { showErr(errS, "Session name is required."); name.classList.add("error"); ok = false; }
    else { showErr(errS, null); name.classList.remove("error"); }
    if (!camp.value.trim()) { showErr(errC, "Campaign name is required."); camp.classList.add("error"); ok = false; }
    else { showErr(errC, null); camp.classList.remove("error"); }
    if (!added.length) { showErr(errVideos, "Add at least one YouTube link."); ok = false; }
    else showErr(errVideos, null);
    if (!ok) return;
    try {
      const { session, campaign } = await demoApi.createSession({
        name: name.value, campaignName: camp.value, videoUrls: added,
      });
      location.hash = `#/sessions/${session.id}/campaigns/${campaign.id}`;
    } catch (err) {
      showErr(errVideos, err.message);
    }
  });
}

/* ---------- Campaign detail ---------- */
async function renderCampaign(sessionId, campaignId) {
  setSidebarActive("sessions");
  const [session, campaign] = await Promise.all([
    demoApi.getSession(sessionId), demoApi.getCampaign(campaignId),
  ]);
  const briefPoints = latestBriefPointsFor(sessionId);
  const runningRun = [...store.runs.values()].find((r) => r.sessionId === sessionId && r.status === "running");

  setTopbar(`
    <div class="topbar-left">
      <nav class="crumb" aria-label="Breadcrumb">
        <a href="#/sessions">${esc(session.name)}</a>
        <span class="sep">/</span>
        <span class="here">${esc(campaign.name)}</span>
        <span class="badge${session.status === "complete" ? " neutral" : ""}">${esc(STATUS_LABEL[session.status] || "Draft")}</span>
      </nav>
    </div>
    <div class="topbar-right">
      ${disWrap('<button class="btn secondary" type="button" disabled>Settings</button>', "Session settings are not available in this local demo.")}
      <button class="btn primary" type="button" id="btn-run">${runningRun ? "Run in progress…" : "Run analysis"}</button>
    </div>`);
  const runBtn = document.getElementById("btn-run");
  if (runningRun) {
    runBtn.disabled = false;
    runBtn.addEventListener("click", () => { location.hash = `#/runs/${runningRun.id}`; });
  } else {
    runBtn.addEventListener("click", async () => {
      try {
        const run = await demoApi.startRun(sessionId);
        location.hash = `#/runs/${run.id}`;
      } catch (err) { alert(err.message); }
    });
  }

  const totalComments = campaign.videos.reduce((a, v) => a + v.commentCount, 0);
  view.innerHTML = `
  <div class="campaign-layout">
    <div class="campaign-main">
      <section class="discovery" aria-labelledby="discovery-h">
        <div class="head">
          <span class="star">${ICONS.star}</span>
          <h2 id="discovery-h">Source discovery</h2>
        </div>
        <p class="unavail">Automatic source discovery is not available in this local demo. Add individual YouTube URLs below.</p>
        <div class="fake-input" aria-disabled="true">
          <span>e.g. "find reaction videos to ${esc(campaign.name)} with more than 500 comments"</span>
          <span>${ICONS.send}</span>
        </div>
      </section>

      <section aria-labelledby="videos-h" style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div class="panel-title" id="videos-h">YouTube links <span style="color:var(--quiet);font-weight:600">· ${campaign.videos.length}</span></div>
          <div style="font-size:12.5px;color:var(--muted)">${fmtNum(totalComments)} comments available</div>
        </div>
        <div class="video-list">
          ${campaign.videos.map((v) => `
          <div class="video-row">
            <div class="video-thumb" aria-hidden="true">${ICONS.play}</div>
            <div class="video-info">
              <div class="video-title">${esc(v.title)}</div>
              <div class="video-meta">${esc(v.channel)} · ${fmtNum(v.commentCount)} comments · demo metadata</div>
            </div>
            ${v.kind === "short" ? '<span class="kind-tag short">Short</span>' : '<span class="kind-tag">Video</span>'}
            <button class="icon-btn" type="button" data-rm-video="${v.id}" aria-label="Remove ${esc(v.title)}">${ICONS.x}</button>
          </div>`).join("")}
          ${campaign.videos.length === 0 ? '<div style="padding:18px 16px;font-size:13px;color:var(--muted)">No videos yet — add at least one before running an analysis.</div>' : ""}
        </div>
        <div class="url-add">
          <input class="input" id="c-url" type="url" autocomplete="off" placeholder="Paste a YouTube link" aria-label="Add a YouTube link">
          <button class="btn secondary" type="button" id="c-add-url">Add link</button>
        </div>
        <div class="field-error" id="c-url-err" hidden></div>
      </section>
    </div>

    <div class="campaign-rail">
      <section aria-labelledby="assets-h" style="display:flex;flex-direction:column;gap:12px">
        <div class="panel-title" id="assets-h">Campaign assets</div>
        <div class="dropzone" id="dropzone" role="button" tabindex="0" aria-label="Upload a file: PDF, PPTX, DOCX, PNG, JPG or WEBP up to 10 megabytes">
          <span class="up">${ICONS.up}</span>
          <span class="t">Drop briefs, decks or key visuals</span>
          <span class="d">PDF, PPTX, DOCX, JPG, PNG, WEBP up to 10 MB — or paste an article URL below</span>
          <span class="d">Disallowed types bounce off; nothing is uploaded until it passes.</span>
        </div>
        <input type="file" id="file-input" hidden multiple>
        <div class="field-error" id="asset-err" hidden></div>
        <div class="url-add">
          <input class="input" id="c-article" type="url" autocomplete="off" placeholder="Paste an article URL" aria-label="Add an article URL">
          <button class="btn secondary" type="button" id="c-add-article">Add article</button>
        </div>
        <div class="field-error" id="article-err" hidden></div>
        <div style="display:flex;flex-direction:column;gap:8px" id="asset-list">
          ${campaign.assets.map((a) => assetRowHtml(a)).join("")}
        </div>
        <div class="notice">Scanned documents are kept, but OCR is unavailable in this local demo — image-only PDFs add no text to the brief context.</div>
      </section>

      <section class="card" aria-labelledby="ideas-h" style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;flex-direction:column;gap:4px">
          <div class="panel-title" id="ideas-h">Ideas we'll test for transfer</div>
          <div class="panel-note">Pulled from your brief during a run. Edit them when the run pauses for review.</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:7px">
          ${briefPoints.length ? briefPoints.map((p, i) => `
            <div class="idea-row">
              <span class="num">${String(i + 1).padStart(2, "0")}</span>
              <span class="txt">${esc(p.label)}</span>
            </div>`).join("") : `
            <div class="idea-row"><span class="txt" style="color:var(--quiet)">Nothing here yet. Start a run and we'll pull the ideas out of your brief for you to confirm.</span></div>`}
        </div>
      </section>
    </div>
  </div>`;

  // Videos
  const urlInput = document.getElementById("c-url");
  const urlErr = document.getElementById("c-url-err");
  const addUrl = async () => {
    try {
      await demoApi.addVideo(campaignId, urlInput.value);
      renderCampaign(sessionId, campaignId);
    } catch (err) {
      urlErr.textContent = err.message; urlErr.hidden = false;
      urlInput.classList.add("error");
    }
  };
  document.getElementById("c-add-url").addEventListener("click", addUrl);
  urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addUrl(); } });
  view.querySelectorAll("[data-rm-video]").forEach((b) => {
    b.addEventListener("click", async () => {
      await demoApi.removeVideo(b.dataset.rmVideo);
      renderCampaign(sessionId, campaignId);
    });
  });

  // Assets
  const dz = document.getElementById("dropzone");
  const fi = document.getElementById("file-input");
  const assetErr = document.getElementById("asset-err");
  const uploadFiles = async (files) => {
    assetErr.hidden = true;
    let failed = false;
    for (const f of files) {
      try { await demoApi.uploadAsset(campaignId, f); }
      catch (err) { assetErr.textContent = err.message; assetErr.hidden = false; failed = true; }
    }
    if (failed) {
      // Keep the message visible across the rerender: the error element is
      // recreated, so carry the text through the render.
      const msg = assetErr.textContent;
      await renderCampaign(sessionId, campaignId);
      const el = document.getElementById("asset-err");
      if (el && msg) { el.textContent = msg; el.hidden = false; }
      return;
    }
    renderCampaign(sessionId, campaignId);
  };
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fi.click(); } });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault(); dz.classList.remove("drag");
    uploadFiles(e.dataTransfer.files);
  });
  fi.addEventListener("change", () => uploadFiles(fi.files));

  const artInput = document.getElementById("c-article");
  const artErr = document.getElementById("article-err");
  const addArticle = async () => {
    try {
      await demoApi.addArticle(campaignId, artInput.value);
      renderCampaign(sessionId, campaignId);
    } catch (err) {
      artErr.textContent = err.message; artErr.hidden = false;
      artInput.classList.add("error");
    }
  };
  document.getElementById("c-add-article").addEventListener("click", addArticle);
  artInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addArticle(); } });

  view.querySelectorAll("[data-rm-asset]").forEach((b) => {
    b.addEventListener("click", async () => {
      await demoApi.removeAsset(b.dataset.rmAsset);
      renderCampaign(sessionId, campaignId);
    });
  });
  view.querySelectorAll("[data-kv]").forEach((b) => {
    b.addEventListener("click", async () => {
      try { await demoApi.setKeyVisual(campaignId, b.dataset.kv); }
      catch (err) { assetErr.textContent = err.message; assetErr.hidden = false; }
      renderCampaign(sessionId, campaignId);
    });
  });
}

function assetRowHtml(a) {
  const ext = a.kind === "article" ? "" : (a.name.split(".").pop() || "").toUpperCase().slice(0, 4);
  const icon = a.kind === "article"
    ? `<div class="asset-icon">${ICONS.link}</div>`
    : a.kind === "image"
      ? `<div class="asset-icon img" aria-hidden="true"></div>`
      : `<div class="asset-icon">${esc(ext)}</div>`;
  const sub = a.kind === "article"
    ? `<div class="asset-sub ok">Linked · added to context at run time</div>`
    : a.isKeyVisual
      ? `<div class="asset-sub ok">Used as the key visual in the report</div>`
      : `<div class="asset-sub">${a.kind === "image" ? fmtSize(a.size) + " · image context" : fmtSize(a.size) + " · text added to context at run time"}</div>`;
  return `
  <div class="asset-row">
    ${icon}
    <div class="asset-info">
      <div class="asset-name" title="${esc(a.name)}">${esc(a.name)}</div>
      ${sub}
    </div>
    ${a.kind === "image" ? `<button class="kv-toggle ${a.isKeyVisual ? "on" : ""}" type="button" data-kv="${a.id}" ${a.isKeyVisual ? "aria-pressed=\"true\"" : ""}>${a.isKeyVisual ? "Key visual" : "Set as key visual"}</button>` : ""}
    <button class="icon-btn" type="button" data-rm-asset="${a.id}" aria-label="Remove ${esc(a.name)}">${ICONS.x}</button>
  </div>`;
}

function latestBriefPointsFor(sessionId) {
  const runs = [...store.runs.values()]
    .filter((r) => r.sessionId === sessionId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  if (!runs.length) return [];
  return runs[0].briefPointIds.map((id) => store.briefPoints.get(id)).filter(Boolean)
    .sort((a, b) => a.order - b.order);
}

/* ---------- Run ---------- */
const STEP_DEFS = [
  { key: "collect", label: "Collected the comments", pending: "Fetching every comment and reply" },
  { key: "themes", label: "Found what people are talking about", pending: "Reading a sample to find the themes" },
  { key: "classify", label: "Labelling every comment", pending: "Applying the codebook to the full set" },
  { key: "emotion", label: "Double-checking the leftovers", pending: "If too much lands in \"Other\", we look for missed themes" },
  { key: "report", label: "Writing your note", pending: "Two charts, the verdicts, and the quotes behind them" },
];
const STAGE_TO_STEP = { connecting: -1, collect: 0, brief: 0, brief_pause: 1, classify: 2, emotion: 3, report: 4, complete: 5, failed: -2 };

async function renderRun(runId) {
  setSidebarActive("sessions");
  const run = await demoApi.getRun(runId);
  const session = await demoApi.getSession(run.sessionId);
  const campaign = store.campaigns.get(session.campaignIds[0]);

  setTopbar(`
    <div class="topbar-left">
      <nav class="crumb" aria-label="Breadcrumb">
        <a href="#/sessions/${session.id}/campaigns/${campaign ? campaign.id : ""}">${esc(session.name)}</a>
        <span class="sep">/</span>
        <span class="here">${esc(campaign ? campaign.name : "Campaign")}</span>
        <span class="badge" id="run-badge">${run.status === "failed" ? "Failed" : run.status === "complete" ? "Complete" : "Running"}</span>
      </nav>
    </div>
    <div class="topbar-right">
      <span class="topbar-org" style="font-size:12px">No cancellation in this demo — a run always finishes.</span>
    </div>`);

  view.innerHTML = `
  <div class="run-layout">
    <div class="run-main">
      <div style="display:flex;flex-direction:column;gap:6px" aria-live="polite">
        <h1 class="run-title" id="run-title">Getting ready…</h1>
        <p class="run-sub" id="run-sub">This demo runs on fixture data in about half a minute.</p>
      </div>
      <div id="run-banner"></div>
      <div id="brief-review" hidden></div>
      <div class="stepper" id="stepper"></div>
      <details class="demo-controls">
        <summary>Demo controls</summary>
        <div class="inner">
          <span>These buttons exist only because this demo has no real backend to fail on its own.</span>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn secondary" type="button" id="demo-disconnect">Simulate disconnect</button>
            <button class="btn secondary danger" type="button" id="demo-fail">Simulate failure</button>
          </div>
        </div>
      </details>
    </div>
    <div class="run-rail">
      <div class="rail-card">
        <div class="rail-kicker">LIVE COUNTS</div>
        <div style="display:flex;flex-direction:column;gap:13px">
          <div class="count-row"><span class="k">Comments labelled</span><span class="v" id="cnt-labelled">0</span></div>
          <div class="count-row"><span class="k">Themes in play</span><span class="v" id="cnt-themes">—</span></div>
          <div class="count-row"><span class="k">Landing in "Other"</span><span class="v pink" id="cnt-other">—</span></div>
        </div>
      </div>
      <div class="notice pink">
        <strong>Early read.</strong> Street-football nostalgia tends to lead. The full picture lands with the note.
      </div>
    </div>
  </div>`;

  const stepperEl = document.getElementById("stepper");
  const titleEl = document.getElementById("run-title");
  const subEl = document.getElementById("run-sub");
  const bannerEl = document.getElementById("run-banner");
  const briefEl = document.getElementById("brief-review");
  const badgeEl = document.getElementById("run-badge");
  const cntLabelled = document.getElementById("cnt-labelled");
  const cntThemes = document.getElementById("cnt-themes");
  const cntOther = document.getElementById("cnt-other");

  let state = { stage: "connecting", pct: 0, detail: {}, disconnected: false, failed: null, completed: false };
  let currentStep = -1;
  let reconnectAttempts = 0;
  let reconnectTimer = null;
  let briefRendered = false;

  function paintSteps() {
    const d = state.detail || {};
    const details = [
      state.stage === "collect" || currentStep > 0
        ? `${fmtNum(d.collected || (currentStep > 0 ? totalComments() : 0))} across ${d.videos || videoCount()} videos, replies included`
        : STEP_DEFS[0].pending,
      currentStep > 1 ? "7 themes, drawn from a 640-comment read" : STEP_DEFS[1].pending,
      state.stage === "classify"
        ? `${fmtNum(d.labelled || 0)} of ${fmtNum(d.total || totalComments())} · batch ${d.batch || 1} of ${d.batches || 1}`
        : currentStep > 2 ? `${fmtNum(totalComments())} labelled` : STEP_DEFS[2].pending,
      currentStep > 3 ? "Theme list holds — \"Other\" stayed low" : STEP_DEFS[3].pending,
      currentStep > 4 ? "Note written" : STEP_DEFS[4].pending,
    ];
    stepperEl.innerHTML = STEP_DEFS.map((s, i) => {
      const done = currentStep > i || state.completed;
      const cur = !state.completed && currentStep === i && !state.failed;
      const glyph = done
        ? `<div class="step-dot done">${ICONS.check}</div>`
        : cur ? `<div class="step-dot current" role="img" aria-label="In progress"></div>` : `<div class="step-dot"></div>`;
      const line = i < STEP_DEFS.length - 1 ? `<div class="step-line"></div>` : "";
      const bar = cur && state.stage === "classify"
        ? `<div class="progressbar"><div style="width:${Math.round(100 * (d.labelled || 0) / (d.total || 1))}%"></div></div>`
        : "";
      return `
      <div class="step-row">
        <div class="step-glyph">${glyph}${line}</div>
        <div class="step-body">
          <div class="step-name ${done || cur ? "" : "pending"}">${s.label}</div>
          <div class="step-detail ${done || cur ? "" : "pending"}">${details[i]}</div>
          ${bar}
        </div>
      </div>`;
    }).join("");
  }

  function totalComments() {
    return (state.detail && state.detail.total) || initialTotal;
  }
  function videoCount() {
    return (state.detail && state.detail.videos) || (campaign ? campaign.videoIds.length : 1);
  }

  const initialTotal = campaign
    ? Math.max(1200, campaign.videoIds.map((id) => store.videos.get(id)).filter(Boolean).reduce((a, v) => a + v.commentCount, 0))
    : 8412;

  function paintHeader() {
    if (state.failed) {
      titleEl.textContent = "This run stopped";
      subEl.textContent = "Nothing was written. Your campaign setup is untouched.";
      badgeEl.textContent = "Failed";
      bannerEl.innerHTML = `
        <div class="banner error" role="alert">
          <div style="flex:1">${esc(state.failed)}</div>
        </div>
        <div style="display:flex;gap:10px;margin-top:12px">
          <a class="btn secondary" href="#/sessions/${session.id}/campaigns/${campaign ? campaign.id : ""}">Return to campaign</a>
          <button class="btn primary" type="button" id="btn-fresh-run">Start a fresh run</button>
        </div>`;
      const fresh = document.getElementById("btn-fresh-run");
      if (fresh) fresh.addEventListener("click", async () => {
        const r = await demoApi.startRun(session.id);
        location.hash = `#/runs/${r.id}`;
      });
    } else if (state.completed) {
      titleEl.textContent = "Your note is ready";
      subEl.textContent = "Two charts, the written read, and the comments behind every number.";
      badgeEl.textContent = "Complete";
      bannerEl.innerHTML = `
        <div class="banner warn" role="status">
          <div style="flex:1">Run complete.</div>
          <a class="btn primary" href="#/runs/${runId}/results" id="btn-results">Open the strategy note</a>
        </div>`;
    } else if (state.disconnected) {
      titleEl.textContent = "Reconnecting…";
      subEl.textContent = "The connection dropped. The analysis is still running — progress picks up where it left off.";
      bannerEl.innerHTML = `
        <div class="banner warn" role="status">
          <span class="spinner sm" aria-hidden="true"></span>
          <div style="flex:1">Connection lost — retrying${reconnectAttempts ? ` (attempt ${reconnectAttempts})` : ""}. No progress is lost.</div>
        </div>`;
    } else {
      const msg = {
        connecting: "Connecting…", collect: "Reading " + fmtNum(totalComments()) + " comments",
        brief: "Reading the brief…", brief_pause: "Confirm the ideas before we label",
        classify: "Reading " + fmtNum(totalComments()) + " comments",
        emotion: "Reading " + fmtNum(totalComments()) + " comments",
        report: "Reading " + fmtNum(totalComments()) + " comments",
      }[state.stage] || "Working…";
      titleEl.textContent = msg;
      subEl.textContent = state.stage === "brief_pause"
        ? "This is the one decision point. Everything after this is automatic."
        : "You can leave the page — in this demo the run finishes in under a minute.";
      if (state.stage !== "brief_pause") bannerEl.innerHTML = "";
    }
  }

  function renderBriefReview() {
    const points = run.briefPointIds.map((id) => store.briefPoints.get(id)).filter(Boolean)
      .sort((a, b) => a.order - b.order)
      .map((p) => ({ ...p })); // local working copy; store untouched until Confirm
    briefEl.hidden = false;

    function paint() {
      briefEl.innerHTML = `
      <section class="card" aria-labelledby="brief-h" style="border-color:var(--pink-border);display:flex;flex-direction:column;gap:14px">
        <div style="display:flex;flex-direction:column;gap:4px">
          <h2 class="panel-title" id="brief-h" style="font-size:16px">Ideas we'll test for transfer</h2>
          <div class="panel-note">Pulled from your brief. Edit, reorder, drop or add — these are what we look for in the comments. Nothing is saved until you confirm.</div>
        </div>
        <div class="brief-list">
          ${points.map((p, i) => `
          <div class="brief-item ${p.included ? "" : "excluded"}" data-idx="${i}">
            <div class="top">
              <div class="fields">
                <label class="sr-only" for="bp-label-${i}">Idea ${i + 1} label</label>
                <input class="label-in" id="bp-label-${i}" data-f="label" data-i="${i}" value="${esc(p.label)}">
                <label class="sr-only" for="bp-desc-${i}">Idea ${i + 1} description</label>
                <textarea class="desc-in" id="bp-desc-${i}" data-f="description" data-i="${i}" rows="2">${esc(p.description)}</textarea>
              </div>
              <div class="brief-tools">
                <button class="icon-btn" type="button" data-up="${i}" aria-label="Move idea ${i + 1} up" ${i === 0 ? "disabled style=\"opacity:.35;cursor:default\"" : ""}>${ICONS.upArr}</button>
                <button class="icon-btn" type="button" data-down="${i}" aria-label="Move idea ${i + 1} down" ${i === points.length - 1 ? "disabled style=\"opacity:.35;cursor:default\"" : ""}>${ICONS.downArr}</button>
                <button class="icon-btn" type="button" data-del="${i}" aria-label="Delete idea ${i + 1}">${ICONS.x}</button>
              </div>
            </div>
            <label class="switch">
              <input type="checkbox" data-inc="${i}" ${p.included ? "checked" : ""}>
              <span class="track" aria-hidden="true"></span>
              <span class="sw-label">${p.included ? "Included" : "Excluded"}</span>
            </label>
          </div>`).join("")}
        </div>
        <button class="add-line" type="button" id="bp-add">${ICONS.plusSm}<span>Add an idea</span></button>
        <div class="field-error" id="bp-err" hidden></div>
        <div class="brief-foot">
          <button class="btn primary lg" type="button" id="bp-confirm">Confirm and continue</button>
          <span class="panel-note">At least one idea must stay included.</span>
        </div>
      </section>`;

      briefEl.querySelectorAll("[data-f]").forEach((inp) => {
        inp.addEventListener("input", () => { points[Number(inp.dataset.i)][inp.dataset.f] = inp.value; });
      });
      briefEl.querySelectorAll("[data-inc]").forEach((t) => {
        t.addEventListener("change", () => { points[Number(t.dataset.inc)].included = t.checked; paint(); });
      });
      briefEl.querySelectorAll("[data-up]").forEach((b) => b.addEventListener("click", () => {
        const i = Number(b.dataset.up);
        [points[i - 1], points[i]] = [points[i], points[i - 1]];
        paint();
      }));
      briefEl.querySelectorAll("[data-down]").forEach((b) => b.addEventListener("click", () => {
        const i = Number(b.dataset.down);
        [points[i + 1], points[i]] = [points[i], points[i + 1]];
        paint();
      }));
      briefEl.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
        points.splice(Number(b.dataset.del), 1);
        paint();
      }));
      briefEl.querySelector("#bp-add").addEventListener("click", () => {
        points.push({ id: uid(), label: "", description: "", included: true, order: points.length + 1 });
        paint();
        const last = briefEl.querySelector(`#bp-label-${points.length - 1}`);
        if (last) last.focus();
      });
      briefEl.querySelector("#bp-confirm").addEventListener("click", async () => {
        const errEl = briefEl.querySelector("#bp-err");
        const payload = points.map((p, i) => ({ ...p, order: i + 1 }));
        try {
          await demoApi.updateBriefPoints(runId, payload);
          await demoApi.proceedRun(runId);
          briefEl.hidden = true;
          briefEl.innerHTML = "";
        } catch (err) {
          errEl.textContent = err.message; errEl.hidden = false;
        }
      });
    }
    paint();
    const first = briefEl.querySelector(".label-in");
    if (first) first.focus();
  }

  function onEvent(e) {
    state.stage = e.stage;
    state.pct = e.pct;
    state.detail = e.detail || {};
    if (e.detail && e.detail.error) state.failed = e.detail.error;
    if (e.stage === "failed") { state.failed = state.failed || e.message; }
    if (e.stage === "complete") { state.completed = true; }

    const stepIdx = STAGE_TO_STEP[e.stage];
    if (stepIdx >= 0) currentStep = Math.max(currentStep, stepIdx);
    if (e.stage === "complete") currentStep = 5;

    if (e.detail) {
      if (e.detail.labelled != null) cntLabelled.textContent = fmtNum(e.detail.labelled);
      else if (state.completed) cntLabelled.textContent = fmtNum(totalComments());
      if (currentStep >= 1) cntThemes.textContent = "7";
      if (e.detail.other != null) cntOther.textContent = e.detail.other + "%";
      else if (currentStep >= 3) cntOther.textContent = "6%";
    }

    if (e.stage === "brief_pause" && !briefRendered) {
      briefRendered = true;
      renderBriefReview();
    }
    if (e.stage === "classify" && briefRendered) {
      briefEl.hidden = true;
    }
    paintHeader();
    paintSteps();
  }

  function onDisconnect() {
    state.disconnected = true;
    // Bounded backoff: 1s, 2s, 4s, 8s, then stop trying and say so.
    const delays = [1000, 2000, 4000, 8000];
    let attempt = 0;
    const tryReconnect = () => {
      if (!state.disconnected) return;
      attempt += 1;
      reconnectAttempts = attempt;
      paintHeader();
      if (attempt < delays.length) {
        reconnectTimer = setTimeout(tryReconnect, delays[attempt]);
      }
    };
    reconnectTimer = setTimeout(tryReconnect, delays[0]);
    paintHeader();
  }

  function onReconnect() {
    state.disconnected = false;
    reconnectAttempts = 0;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    bannerEl.innerHTML = "";
    paintHeader();
    paintSteps();
  }

  const unsubscribe = demoApi.subscribeRun(runId, { onEvent, onDisconnect, onReconnect });

  document.getElementById("demo-disconnect").addEventListener("click", () => {
    demoApi.simulateDisconnect(runId);
  });
  document.getElementById("demo-fail").addEventListener("click", () => {
    demoApi.simulateFailure(runId);
  });

  routeCleanup = () => {
    unsubscribe();
    if (reconnectTimer) clearTimeout(reconnectTimer);
  };
}

/* ---------- Results ---------- */
let evDrawerState = null;

function closeEvidenceDrawer() {
  if (!evDrawerState) return;
  const { root, prevFocus, keyHandler } = evDrawerState;
  document.removeEventListener("keydown", keyHandler);
  root.remove();
  if (prevFocus && prevFocus.focus) prevFocus.focus();
  evDrawerState = null;
}

function openEvidenceDrawer(report, metric, originEl) {
  closeEvidenceDrawer();
  const prevFocus = originEl || document.activeElement;
  const overlay = window.matchMedia("(max-width: 1179px)").matches;
  const host = overlay ? overlayRoot : view.querySelector(".report-layout");

  const root = document.createElement(overlay ? "div" : "div");
  root.style.display = "contents";
  root.innerHTML = `
    ${overlay ? '<div class="ev-backdrop" data-ev-backdrop></div>' : ""}
    <aside class="ev-drawer ${overlay ? "overlay" : ""}" role="dialog" aria-modal="${overlay}" aria-label="Evidence for ${esc(metric.label)}">
      <div class="ev-head">
        <div class="ev-title-row">
          <div class="ev-id">
            <span class="ev-kicker">${metric.id.startsWith("m-t-") ? "IDEA FROM YOUR BRIEF" : "THEME"}</span>
            <span class="ev-title">${esc(metric.label)}</span>
            <span class="ev-sub">${fmtNum(metric.evidenceCount)} of ${fmtNum(report._totalComments || 8412)} comments · ${metric.value}%</span>
          </div>
          <button class="ev-close" type="button" data-ev-close aria-label="Close evidence">${ICONS.xLg}</button>
        </div>
        <div class="pill-row" role="group" aria-label="Evidence filters">
          <button class="pill sm active" type="button" data-filter="All" aria-pressed="true">All</button>
          <button class="pill sm" type="button" data-filter="Joy" aria-pressed="false">Joy</button>
          <button class="pill sm" type="button" data-filter="Skeptical" aria-pressed="false">Skeptical</button>
          <button class="pill sm" type="button" data-filter="Neutral" aria-pressed="false">Neutral</button>
          <button class="pill sm" type="button" data-filter="Most liked" aria-pressed="false">Most liked</button>
        </div>
      </div>
      <div class="ev-body" data-ev-body></div>
    </aside>`;
  host.appendChild(root);

  const body = root.querySelector("[data-ev-body]");
  const items = report.evidence.filter((e) => e.metricId === metric.id);
  let filter = "All";

  function paintList() {
    let list = items.slice();
    if (filter === "Most liked") list.sort((a, b) => b.likes - a.likes);
    else if (filter !== "All") list = list.filter((e) => e.emotion === filter);
    body.innerHTML = list.length
      ? list.map((e) => `
        <div class="ev-card">
          <div class="ev-text">${esc(e.text)}</div>
          <div class="ev-meta">${esc(e.emotion)} · ${esc(e.author)} · ${fmtNum(e.likes)} likes</div>
        </div>`).join("")
        + `<div class="ev-count">Showing ${list.length} of ${fmtNum(metric.evidenceCount)} labelled comments. Fixture evidence in this demo.</div>`
      : `<div class="ev-empty">No comments match this filter for “${esc(metric.label)}”. Try another filter.</div>`;
  }

  root.querySelectorAll("[data-filter]").forEach((b) => {
    b.addEventListener("click", () => {
      filter = b.dataset.filter;
      root.querySelectorAll("[data-filter]").forEach((x) => {
        const on = x === b;
        x.classList.toggle("active", on);
        x.setAttribute("aria-pressed", on ? "true" : "false");
      });
      paintList();
    });
  });
  root.querySelector("[data-ev-close]").addEventListener("click", closeEvidenceDrawer);
  const backdrop = root.querySelector("[data-ev-backdrop]");
  if (backdrop) backdrop.addEventListener("click", closeEvidenceDrawer);

  const keyHandler = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); closeEvidenceDrawer(); }
    if (e.key === "Tab" && overlay) {
      const f = root.querySelectorAll("button, [href]");
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  document.addEventListener("keydown", keyHandler);

  evDrawerState = { root, prevFocus, keyHandler };
  paintList();
  root.querySelector("[data-ev-close]").focus();
}

async function renderResults(runId) {
  setSidebarActive("sessions");
  let report;
  try {
    report = await demoApi.getReport(runId);
  } catch {
    const run = await demoApi.getRun(runId);
    view.innerHTML = `
    <div class="view-pad">
      <div class="empty-block">
        <h3>No report yet</h3>
        <p>This run ${run.status === "failed" ? "failed before anything was written" : "is still " + esc(run.message.toLowerCase())}.</p>
        <div class="actions"><a class="btn secondary" href="#/runs/${runId}">Back to the run</a></div>
      </div>
    </div>`;
    setTopbar('<div class="topbar-left"><span class="topbar-title">Results</span></div><div class="topbar-right"></div>');
    return;
  }
  const run = await demoApi.getRun(runId);
  const session = await demoApi.getSession(run.sessionId);
  const campaign = store.campaigns.get(session.campaignIds[0]);
  const artifacts = [...store.artifacts.values()].filter((a) => a.runId === runId);
  const pdf = artifacts.find((a) => a.kind === "pdf");
  report._totalComments = session.commentCount || 8412;

  setTopbar(`
    <div class="topbar-left">
      <nav class="crumb" aria-label="Breadcrumb">
        <a href="#/sessions/${session.id}/campaigns/${campaign ? campaign.id : ""}">${esc(session.name)}</a>
        <span class="sep">/</span>
        <span class="here">${esc(campaign ? campaign.name : "Campaign")}</span>
        <span class="badge neutral">Complete</span>
      </nav>
    </div>
    <div class="topbar-right">
      <button class="btn secondary" type="button" id="btn-pdf">Download PDF</button>
    </div>`);

  document.getElementById("btn-pdf").addEventListener("click", () => {
    if (pdf) downloadBlob(pdf.content, pdf.name, "application/pdf");
  });

  const [line1, line2] = report.title.split("\n");
  view.innerHTML = `
  <div class="report-layout">
    <div class="report-scroll">
      <article class="report">
        <div style="display:flex;flex-direction:column;gap:12px">
          <span class="kicker">STRATEGY NOTE · ${new Date(run.createdAt).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }).toUpperCase()}</span>
          <h1>${esc(line1)}<br>${esc(line2 || "")}</h1>
          <div class="meta">${esc(report.subtitle)} · fixture data</div>
        </div>

        <div class="stat-block">
          <div><button class="big-stat" type="button" data-metric="overall">${report.overallTransfer}%</button></div>
          <p class="explain">of comments echoed at least one idea from your brief — but most of those echoed the <strong>place</strong>, not the <strong>product</strong>. Click any number to read the comments behind it.</p>
        </div>

        <section class="chart" aria-labelledby="chart-transfer-h">
          <h2 id="chart-transfer-h">Which ideas arrived</h2>
          <p class="sub">Share of all ${fmtNum(report._totalComments)} comments that echoed each idea in the brief</p>
          <div class="bars">
            ${report.transfers.map((m) => `
            <div class="bar-row">
              <div class="bar-head">
                <span class="bar-label">${esc(m.label)}</span>
                <button class="bar-val" type="button" data-metric="${m.id}">${m.value}%</button>
              </div>
              <div class="bar-track"><div class="bar-fill" style="width:${m.value}%"></div></div>
            </div>`).join("")}
          </div>
          <div class="method-line"><span>Counted from per-comment labels — never estimated.</span><a href="#" data-dl="chart_transfer.csv">chart_transfer.csv ↓</a></div>
        </section>

        <section class="chart" aria-labelledby="chart-themes-h">
          <h2 id="chart-themes-h">What the audience talked about</h2>
          <p class="sub">Every comment carries exactly one theme label</p>
          <div class="bars">
            ${report.themes.map((m) => m.label === "Other" ? `
            <div class="bar-row">
              <div class="bar-head">
                <span class="bar-label dim">Other</span>
                <span class="bar-val static">${m.value}%</span>
              </div>
              <div class="bar-track"><div class="bar-fill dim" style="width:${m.value}%"></div></div>
            </div>` : `
            <div class="bar-row">
              <div class="bar-head">
                <span class="bar-label">${esc(m.label)}</span>
                <button class="bar-val dark" type="button" data-metric="${m.id}">${m.value}%</button>
              </div>
              <div class="bar-track"><div class="bar-fill dark" style="width:${m.value}%"></div></div>
            </div>`).join("")}
          </div>
          <div class="method-line"><a href="#" data-dl="chart_themes.csv">chart_themes.csv ↓</a></div>
        </section>

        <section class="read" aria-labelledby="read-h">
          <h2 id="read-h">What we heard</h2>
          ${report.interpretation.split("\n\n").map((p) => `<p>${esc(p)}</p>`).join("")}
          <blockquote class="quote">"${esc(report.quote.text)}"<span class="attr">${esc(report.quote.attr)}</span></blockquote>
          <div class="caveat"><strong>One caution.</strong> ${esc(report.caveat)}</div>
          <div class="method-line"><span>Every labelled comment is in the full export.</span><a href="#" data-dl="comments.csv">comments.csv ↓</a></div>
        </section>
      </article>
    </div>
  </div>`;

  view.querySelectorAll("[data-metric]").forEach((b) => {
    b.addEventListener("click", () => {
      const id = b.dataset.metric;
      const metric = id === "overall"
        ? { id: report.transfers[0] ? report.transfers[0].id : "none", label: "Overall transfer", value: report.overallTransfer, evidenceCount: Math.round(report.overallTransfer / 100 * report._totalComments) }
        : report.transfers.concat(report.themes).find((m) => m.id === id);
      if (metric && metric.id !== "none") openEvidenceDrawer(report, metric, b);
    });
  });
  view.querySelectorAll("[data-dl]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const art = artifacts.find((x) => x.name === a.dataset.dl);
      if (art) downloadBlob(art.content, art.name, "text/csv");
    });
  });

  routeCleanup = () => closeEvidenceDrawer();
}

/* ---------- Files ---------- */
let filesFilter = "all";

async function renderFiles() {
  setSidebarActive("files");
  setTopbar(`
    <div class="topbar-left"><span class="topbar-title">Files</span></div>
    <div class="topbar-right">
      ${disWrap(`<span class="searchbox" aria-disabled="true">${ICONS.search}<span>Search files</span></span>`,
        "Search is not available in this local demo.")}
    </div>`);

  const files = await demoApi.listFiles();
  const shown = files.filter((f) =>
    filesFilter === "all" ? true : filesFilter === "added" ? f._file === "asset" : f._file === "artifact");

  const rows = shown.map((f) => {
    const campaign = store.campaigns.get(f.campaignId);
    const isArtifact = f._file === "artifact";
    const ext = isArtifact ? (f.kind === "pdf" ? "PDF" : "CSV")
      : f.kind === "article" ? null : (f.name.split(".").pop() || "").toUpperCase().slice(0, 4);
    const icon = f.kind === "article"
      ? `<div class="file-icon">${ICONS.link}</div>`
      : f.kind === "image" ? `<div class="file-icon img"></div>`
      : `<div class="file-icon">${esc(ext || "")}</div>`;
    const canOpen = isArtifact ? f.kind === "pdf" : f.kind === "image";
    const action = canOpen
      ? `<button class="file-open" type="button" data-open="${f.id}" data-kind="${f._file}">Open</button>`
      : `<button class="file-open" type="button" data-dl-file="${f.id}" data-kind="${f._file}">Download</button>`;
    return `
    <div class="trow" style="grid-template-columns:1.7fr 1.4fr .7fr .6fr .8fr 80px">
      <div style="display:flex;gap:11px;align-items:center;min-width:0">
        ${icon}
        <div style="font-size:13.5px;font-weight:700;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(f.name)}">${esc(f.name)}</div>
      </div>
      <div class="trow-dim">${esc(campaign ? campaign.name : "—")}</div>
      <div>${isArtifact ? '<span class="badge">We made</span>' : '<span class="badge outline">You added</span>'}</div>
      <div class="trow-dim">${fmtSize(f.size)}</div>
      <div class="trow-dim">${fmtAgo(f.addedAt)}</div>
      <div>${action}</div>
    </div>`;
  });

  view.innerHTML = `
  <div class="view-pad" style="gap:20px">
    <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div style="display:flex;flex-direction:column;gap:5px">
        <h2 class="greeting">Files</h2>
        <div class="greeting-sub">Everything you gave us, and everything we made — across all sessions in this demo run.</div>
      </div>
      <div class="pill-row" role="group" aria-label="File filters">
        <button class="pill ${filesFilter === "all" ? "active" : ""}" type="button" data-ff="all" aria-pressed="${filesFilter === "all"}">All</button>
        <button class="pill ${filesFilter === "added" ? "active" : ""}" type="button" data-ff="added" aria-pressed="${filesFilter === "added"}">You added</button>
        <button class="pill ${filesFilter === "made" ? "active" : ""}" type="button" data-ff="made" aria-pressed="${filesFilter === "made"}">We made</button>
      </div>
    </div>
    ${files.length === 0 ? `
    <div class="empty-block">
      <div class="empty-icon">${ICONS.folder}</div>
      <h3>No files yet</h3>
      <p>Upload briefs and key visuals to a campaign, or run an analysis — reports and CSVs land here.</p>
      <div class="actions"><a class="btn primary" href="#/sessions/new">Create a session</a></div>
    </div>` : `
    <div class="table-scroll">
    <div class="table" style="min-width:820px">
      <div class="thead" style="grid-template-columns:1.7fr 1.4fr .7fr .6fr .8fr 80px">
        <div>FILE</div><div>CAMPAIGN</div><div>KIND</div><div>SIZE</div><div>ADDED</div><div></div>
      </div>
      ${rows.join("") || '<div style="padding:18px 20px;font-size:13px;color:var(--muted)">Nothing in this filter yet.</div>'}
    </div>
    </div>
    <div style="font-size:12px;color:var(--quiet)">${shown.length} file${shown.length === 1 ? "" : "s"}. Deleting an input you added won't change a report that has already run — reports are immutable.</div>`}
  </div>`;

  view.querySelectorAll("[data-ff]").forEach((b) => {
    b.addEventListener("click", () => { filesFilter = b.dataset.ff; renderFiles(); });
  });
  view.querySelectorAll("[data-dl-file]").forEach((b) => {
    b.addEventListener("click", async () => {
      if (b.dataset.kind === "artifact") {
        const a = await demoApi.getArtifact(b.dataset.dlFile);
        downloadBlob(a.content, a.name, a.kind === "pdf" ? "application/pdf" : "text/csv");
      } else {
        const blob = await demoApi.getAssetData(b.dataset.dlFile);
        const a = store.assets.get(b.dataset.dlFile);
        if (blob && a) downloadBlob(blob, a.name, a.mimeType);
        else if (a && a.sourceUrl) window.open(a.sourceUrl, "_blank", "noopener");
      }
    });
  });
  view.querySelectorAll("[data-open]").forEach((b) => {
    b.addEventListener("click", async () => {
      if (b.dataset.kind === "artifact") {
        const a = await demoApi.getArtifact(b.dataset.open);
        const url = URL.createObjectURL(new Blob([a.content], { type: "application/pdf" }));
        openModal(a.name + " (demo data)", `<iframe src="${url}" title="Preview of ${esc(a.name)}"></iframe>`, url);
      } else {
        const blob = await demoApi.getAssetData(b.dataset.open);
        const a = store.assets.get(b.dataset.open);
        if (blob && a) {
          const url = URL.createObjectURL(blob);
          openModal(a.name, `<img src="${url}" alt="Preview of ${esc(a.name)}">`, url);
        }
      }
    });
  });
}

/* ============================== Router ============================== */

async function route() {
  cleanupRoute();
  const hash = location.hash || "#/home";
  const parts = hash.replace(/^#\//, "").split("/").filter(Boolean);
  view.focus({ preventScroll: true });
  try {
    if (parts.length === 0 || parts[0] === "home") await renderHome();
    else if (parts[0] === "sessions" && parts.length === 1) await renderSessions();
    else if (parts[0] === "sessions" && parts[1] === "new") await renderNewSession();
    else if (parts[0] === "sessions" && parts[2] === "campaigns") await renderCampaign(parts[1], parts[3]);
    else if (parts[0] === "runs" && parts.length === 2) await renderRun(parts[1]);
    else if (parts[0] === "runs" && parts[2] === "results") await renderResults(parts[1]);
    else if (parts[0] === "files") await renderFiles();
    else await renderHome();
  } catch (err) {
    setTopbar('<div class="topbar-left"><span class="topbar-title">Resonance</span></div><div class="topbar-right"></div>');
    view.innerHTML = `
    <div class="view-pad">
      <div class="empty-block">
        <h3>That page is not available</h3>
        <p>${esc(err.message || "Unknown route")}</p>
        <div class="actions"><a class="btn primary" href="#/home">Back home</a></div>
      </div>
    </div>`;
  }
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && modalState) closeModal();
});
// Only boot the UI when the app shell is mounted. self-check.html loads this
// file for the store/demoApi assertions but has no #view/#topbar shell.
if (view && topbar && overlayRoot) route();

})();
