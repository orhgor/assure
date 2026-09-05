/**
 * Edge PDF upload — R2 via Cloudflare Worker, then EC2 substrate ingest.
 */
(function (global) {
  "use strict";

  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MAX_PAGES_DOC = 5;

  function tf(key, fallback, params) {
    if (global.AssureI18n && typeof global.AssureI18n.t === "function") {
      return global.AssureI18n.t(key, fallback, params);
    }
    if (!params) return fallback;
    return fallback.replace(/\{(\w+)\}/g, (_, name) => String(params[name] ?? ""));
  }

  async function countPdfPages(file) {
    const slice = file.slice(0, Math.min(file.size, 5 * 1024 * 1024));
    const buffer = await slice.arrayBuffer();
    const text = new TextDecoder("latin1").decode(buffer);
    const matches = text.match(/\/Type\s*\/Page[^s]/g);
    return matches && matches.length ? matches.length : 1;
  }

  async function uploadPdfViaEdge(file, projectId, workerUrl, onStatus) {
    if (!workerUrl) {
      throw new Error("Edge worker URL is not configured.");
    }
    if (!/\.pdf$/i.test(file.name || "")) {
      throw new Error("Edge upload supports PDF files only.");
    }
    if (file.size > MAX_FILE_BYTES) {
      throw new Error(
        tf("upload.limits.size", "File exceeds {max_mb} MB limit. Please compress or split the document.", {
          max_mb: 10,
        })
      );
    }
    const pageCount = await countPdfPages(file);
    if (pageCount > MAX_PAGES_DOC) {
      throw new Error(
        tf(
          "upload.edge.limits.pages_doc",
          "PDF exceeds {max_pages} page edge limit. Please upload a shorter extract.",
          { max_pages: MAX_PAGES_DOC }
        )
      );
    }

    const base = workerUrl.replace(/\/$/, "");
    onStatus &&
      onStatus(tf("upload.edge.uploading", "Uploading PDF to secure edge storage…"), "info");

    const urlRes = await fetch(
      `${base}/api/upload-url?projectId=${encodeURIComponent(projectId)}&filename=${encodeURIComponent(file.name || "upload.pdf")}`,
      { method: "GET", credentials: "omit" }
    );
    const urlPayload = await urlRes.json();
    if (!urlRes.ok || !urlPayload.ok) {
      throw new Error((urlPayload && urlPayload.error) || tf("error.server", "Something went wrong. Try again."));
    }

    const putRes = await fetch(urlPayload.uploadUrl, {
      method: "PUT",
      headers: { "Content-Type": "application/pdf" },
      body: file,
    });
    const putPayload = await putRes.json().catch(() => ({}));
    if (!putRes.ok || putPayload.ok === false) {
      throw new Error((putPayload && putPayload.error) || tf("error.server", "Something went wrong. Try again."));
    }

    onStatus &&
      onStatus(tf("upload.edge.processing", "Extracting text at the edge…"), "info");

    const processRes = await fetch(`${base}/api/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: urlPayload.key, projectId }),
    });
    const processPayload = await processRes.json();
    if (!processRes.ok || !processPayload.ok) {
      throw new Error(
        (processPayload && processPayload.error) ||
          tf("upload.edge.failed", "Edge extraction failed. Try again or upload a text-native PDF.")
      );
    }

    onStatus &&
      onStatus(tf("upload.edge.done", "Document text extracted at the edge."), "success");

    return {
      ok: true,
      text: processPayload.text || "",
      pageCount: processPayload.pageCount || pageCount,
      usedTextract: !!processPayload.usedTextract,
    };
  }

  global.AssureEdgeUpload = {
    uploadPdfViaEdge,
    MAX_FILE_BYTES,
    MAX_PAGES_DOC,
  };
})(window);
