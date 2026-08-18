/* live.js - real API implementation for window.__liveApi.
   Loaded before app.js. Defines window.__liveApi so app.js mode-probe picks
   it up. Never modifies demoApi or the store. */
(function () {
"use strict";

/* ---- Error normalisation ----
   FastAPI wraps HTTPException errors in {detail:{error,message,field}}.
   Pydantic validation errors come flat: {error,message,field}.
   Both shapes are converted to Error(message) with .code and .field props,
   matching demoError() in app.js so renderers never branch on error shape. */
function normError(body, status) {
  const inner = (body && typeof body === "object")
    ? (body.detail && typeof body.detail === "object" ? body.detail : body)
    : {};
  const message = inner.message || (typeof body.detail === "string" ? body.detail : null)
    || "Request failed (" + status + ")";
  const e = new Error(message);
  e.error = inner.error || "error";
  e.code  = e.error; // back-compat for existing callers
  e.field = inner.field || null;
  return e;
}

async function apiFetch(path, opts) {
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (netErr) {
    const e = new Error("Network error: " + netErr.message);
    e.code = "network"; e.field = null;
    throw e;
  }
  if (!resp.ok) {
    let body;
    try { body = await resp.json(); } catch { body = {}; }
    throw normError(body, resp.status);
  }
  return resp;
}

async function apiJson(path, opts) {
  const resp = await apiFetch(path, opts);
  return resp.json();
}

/* Parses filename from a Content-Disposition header, quoted or unquoted.
   Matches filename="x.csv" or filename=x.csv; ignores filename*= (RFC 5987)
   since backend always sends plain filename for these artifacts. */
function parseContentDispositionFilename(header) {
  const m = /filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;]+)/i.exec(header);
  if (!m) return null;
  return (m[1] || m[2] || "").trim();
}

/* The six public artifact kinds, in contract order. Mirrors demoApi's
   PUBLIC_ARTIFACTS in app.js; kept independent since live.js loads first
   and must not depend on app.js internals. */
const PUBLIC_ARTIFACT_ORDER = {
  report_pdf: 0, comments_csv: 1, key_messages_csv: 2,
  themes_csv: 3, sentiment_csv: 4, emotions_csv: 5,
};
function filterPublicArtifacts(list) {
  return (list || [])
    .filter((a) => a.kind in PUBLIC_ARTIFACT_ORDER)
    .sort((a, b) => PUBLIC_ARTIFACT_ORDER[a.kind] - PUBLIC_ARTIFACT_ORDER[b.kind]);
}

/* ---- helpers ---- */
function json(body) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function patch(body) {
  return {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/* ============================== __liveApi ============================== */

const liveApi = {

  /* createSession: 3-step flow.
     POST /sessions -> POST /sessions/{id}/campaigns -> N * POST /campaigns/{id}/videos.
     On a video 422 the session and campaign are left on the server (no cleanup). */
  async createSession(input) {
    const session  = await apiJson("/api/sessions", json({ name: input.name }));
    const campaign = await apiJson("/api/sessions/" + session.id + "/campaigns",
      json({ name: input.campaignName }));
    const urls = input.videoUrls || [];
    for (const url of urls) {
      try {
        await apiJson("/api/campaigns/" + campaign.id + "/videos",
          json({ url, kind: "auto" }));
      } catch (err) {
        // ponytail: no partial rollback; session+campaign remain on server.
        err.field = "videos";
        throw err;
      }
    }
    return { session, campaign };
  },

  async addVideo(campaignId, url) {
    return apiJson("/api/campaigns/" + campaignId + "/videos",
      json({ url, kind: "auto" }));
  },

  async removeVideo(videoId) {
    await apiFetch("/api/videos/" + videoId, { method: "DELETE" });
  },

  async uploadAsset(campaignId, file) {
    const fd = new FormData();
    fd.append("file", file);
    return apiJson("/api/campaigns/" + campaignId + "/assets/upload",
      { method: "POST", body: fd });
  },

  async addArticle(campaignId, url) {
    return apiJson("/api/campaigns/" + campaignId + "/assets/article",
      json({ url }));
  },

  async removeAsset(assetId) {
    await apiFetch("/api/assets/" + assetId, { method: "DELETE" });
  },

  /* getCampaign: the live API has no GET /campaigns/{id} route.
     Scan sessions list, find the session whose campaignIds includes the id,
     then fetch the full session and return the matching campaign. */
  async getCampaign(campaignId) {
    const sessions = await apiJson("/api/sessions");
    const ownerSess = sessions.find(
      (s) => s.campaignIds && s.campaignIds.includes(campaignId));
    if (!ownerSess) {
      const e = new Error("Campaign not found.");
      e.code = "not_found"; e.field = null;
      throw e;
    }
    const full = await apiJson("/api/sessions/" + ownerSess.id);
    const camp = (full.campaigns || []).find((c) => c.id === campaignId);
    if (!camp) {
      const e = new Error("Campaign not found in session.");
      e.code = "not_found"; e.field = null;
      throw e;
    }
    return camp;
  },

  async listSessions() {
    return apiJson("/api/sessions");
  },

  async getSession(id) {
    return apiJson("/api/sessions/" + id);
  },

  async getKeyMessages(sessionId) {
    const sess = await apiJson("/api/sessions/" + sessionId);
    return sess.keyMessages;
  },

  async draftKeyMessages(sessionId) {
    return apiJson("/api/sessions/" + sessionId + "/key_messages/draft", { method: "POST" });
  },

  async updateKeyMessages(sessionId, messages) {
    return apiJson("/api/sessions/" + sessionId + "/key_messages", patch({ messages }));
  },

  /* getRunningRun: return latestRun from session when status is queued/running. */
  async getRunningRun(sessionId) {
    const sess = await apiJson("/api/sessions/" + sessionId);
    const lr = sess.latestRun;
    if (lr && (lr.status === "queued" || lr.status === "running")) return lr;
    return null;
  },

  async getRun(id) {
    return apiJson("/api/runs/" + id);
  },

  async startRun(sessionId, skipPause = false) {
    return apiJson("/api/sessions/" + sessionId + "/runs", json({ skipPause: !!skipPause }));
  },

  async updateBriefPoints(runId, messages) {
    return apiJson("/api/runs/" + runId + "/brief_points", patch({ messages }));
  },

  async proceedRun(runId) {
    return apiJson("/api/runs/" + runId + "/proceed", { method: "POST" });
  },

  /* subscribeRun: EventSource on /api/runs/{id}/events.
     Terminal (complete|error): deliver event then close.
     onopen fires on initial connect and on reconnect; track connected state. */
  subscribeRun(id, handlers) {
    let closed = false;
    let wasConnected = false;
    const es = new EventSource("/api/runs/" + id + "/events");

    es.onopen = () => {
      if (wasConnected) {
        handlers.onReconnect && handlers.onReconnect();
      }
      wasConnected = true;
    };

    es.onmessage = (evt) => {
      if (closed) return;
      let data;
      try { data = JSON.parse(evt.data); } catch { return; }
      handlers.onEvent && handlers.onEvent(data);
      if (data.stage === "complete" || data.stage === "error") {
        closed = true;
        // Micro-task so the renderer sees the event before the ES closes.
        setTimeout(() => es.close(), 0);
      }
    };

    es.onerror = () => {
      if (closed) return;
      handlers.onDisconnect && handlers.onDisconnect();
      // Browser reconnects natively; onerror fires again on next failure.
      // We do NOT close here so the native backoff continues.
    };

    return function unsubscribe() {
      closed = true;
      es.close();
    };
  },

  /* getReport: adapts the server's ReportJson to the field names the results
     renderer reads. The renderer was written against the demo fixture, which
     uses different names (transfers/value/id) and carries prose the server
     never produces. Mapping here keeps the renderer shape-agnostic and keeps
     the demo/live boundary in this file.
     ponytail: a one-way field map, not a general adapter layer; if the report
     contract and the renderer converge later, delete this and pass through. */
  async getReport(runId) {
    const raw = await apiJson("/api/runs/" + runId + "/report");
    const keyMessages = Array.isArray(raw.keyMessages) ? raw.keyMessages : [];
    const themes = Array.isArray(raw.themes) ? raw.themes : [];
    const evidence = Array.isArray(raw.evidence) ? raw.evidence : [];
    const flatEvidence = [];
    for (const metric of evidence) {
      const comments = Array.isArray(metric.comments) ? metric.comments : [];
      for (const c of comments) {
        flatEvidence.push({
          metricId: metric.metricId,
          text: c.text,
          likes: c.likes,
          emotion: null,
        });
      }
    }
    const themeCount = themes.length;
    return {
      ...raw,
      title: "Results",
      subtitle: `${keyMessages.length} Key Messages · ${themeCount} ${themeCount === 1 ? "theme" : "themes"}`,
      transfers: keyMessages.map((m) => ({
        id: m.metricId, label: m.label, value: m.percent,
      })),
      themes: themes.map((m) => ({
        id: m.metricId, label: m.label, value: m.percent,
      })),
      evidence: flatEvidence,
    };
  },

  /* getArtifact: the artifact id is known from run.artifacts list.
     We need the runId too - callers pass id only, so we must search.
     ponytail: requires run.artifacts to carry runId on each artifact.
     The server response includes runId on each artifact object. */
  async getArtifact(artifactId) {
    // The caller knows only artifactId. We need runId to build the URL.
    // live callers always have a run in scope - they first call getRun and then
    // iterate run.artifacts which carry runId. But this method signature takes
    // only id. Scan sessions to find the run that owns this artifact.
    // ponytail: O(sessions * runs) scan; acceptable for typical session counts.
    const sessions = await apiJson("/api/sessions");
    for (const sess of sessions) {
      const full = await apiJson("/api/sessions/" + sess.id);
      for (const runSummary of (full.runs || [])) {
        const run = await apiJson("/api/runs/" + runSummary.id);
        const artifact = (run.artifacts || []).find((a) => a.id === artifactId);
        if (artifact) {
          const resp = await apiFetch(
            "/api/runs/" + run.id + "/artifacts/" + artifactId);
          const blob = await resp.blob();
          const disposition = resp.headers.get("Content-Disposition") || "";
          const filename = parseContentDispositionFilename(disposition) || artifact.filename;
          return {
            id: artifactId, runId: run.id, kind: artifact.kind,
            filename, contentType: artifact.contentType, content: blob,
          };
        }
      }
    }
    const e = new Error("Artifact not found.");
    e.code = "not_found"; e.field = null;
    throw e;
  },

  /* getAssetData: GET /api/assets/{id}/file -> blob.
     Returns null for articles (server 404s; we catch and return null). */
  async getAssetData(assetId) {
    try {
      const resp = await apiFetch("/api/assets/" + assetId + "/file");
      return resp.blob();
    } catch (err) {
      if (err.code === "not_found" || (err.message && err.message.includes("404"))) {
        return null;
      }
      throw err;
    }
  },

  /* listFiles: fetch all sessions; compose assets (mark _file:"asset") and
     artifacts from complete runs (mark _file:"artifact"). Sort by addedAt desc. */
  async listFiles() {
    const sessions = await apiJson("/api/sessions");
    const files = [];
    for (const sess of sessions) {
      const full = await apiJson("/api/sessions/" + sess.id);
      const campaignId = (full.campaignIds || [])[0] || null;
      const campaignName = (full.campaigns && full.campaigns[0])
        ? full.campaigns[0].name : null;
      // Assets from campaigns.
      for (const camp of (full.campaigns || [])) {
        for (const asset of (camp.assets || [])) {
          files.push({ ...asset, _file: "asset", campaignName: camp.name });
        }
      }
      // Artifacts from complete runs.
      for (const runSummary of (full.runs || [])) {
        if (runSummary.status !== "complete") continue;
        const run = await apiJson("/api/runs/" + runSummary.id);
        for (const art of filterPublicArtifacts(run.artifacts)) {
          files.push({
            ...art, _file: "artifact",
            campaignId: campaignId, campaignName,
          });
        }
      }
    }
    files.sort((a, b) => (b.addedAt || "").localeCompare(a.addedAt || ""));
    return files;
  },

  async listArtifacts(runId) {
    const run = await this.getRun(runId);
    if (run.status !== "complete") return [];
    return filterPublicArtifacts(run.artifacts);
  },

  /** @param {string} campaignId @param {string} assetId @returns {Promise<never>} */
  setKeyVisual(campaignId, assetId) {
    return Promise.reject({
      error: "FEATURE_UNAVAILABLE",
      message: "Key visuals are not supported.",
      field: null,
    });
  },

  /* These two are demo-only; live mode never calls them but the signatures
     must exist so mode-switched code paths don't throw on property access. */
  async simulateDisconnect() {
    const e = new Error("Not available in live mode");
    e.code = "conflict"; e.field = null;
    throw e;
  },
  async simulateFailure() {
    const e = new Error("Not available in live mode");
    e.code = "conflict"; e.field = null;
    throw e;
  },
};

window.__liveApi = liveApi;

})();
