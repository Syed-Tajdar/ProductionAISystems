// Large uploads: 1. ask the server for a signed upload URL, 2. send the file straight to Supabase
// Storage, 3. ask the server to transcribe and score it from storage. The session cookie authorizes
// steps 1 and 3; the signed URL authorizes step 2 for this one file only.
(function () {
  const form = document.getElementById("upload-form");
  const status = document.getElementById("status");
  const progress = document.getElementById("progress");
  const button = form.querySelector("button");

  function show(text, kind) {
    status.hidden = false;
    status.className = "banner" + (kind ? " " + kind : "");
    status.textContent = text;
  }

  function put(url, file) {
    // XMLHttpRequest rather than fetch, for upload progress. Same request shape as supabase-js.
    return new Promise((resolve, reject) => {
      const body = new FormData();
      body.append("cacheControl", "3600");
      body.append("", file);
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.setRequestHeader("x-upsert", "false");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) progress.value = Math.round((e.loaded / e.total) * 100); };
      xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error("storage answered " + xhr.status)));
      xhr.onerror = () => reject(new Error("network error while uploading"));
      xhr.send(body);
    });
  }

  async function json(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "server answered " + response.status);
    return data;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = form.file.files[0];
    if (!file) return;
    button.disabled = true;
    try {
      show("Preparing the upload...");
      const upload = await json(await fetch("/uploads", {
        method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
        body: JSON.stringify({ filename: file.name, content_type: file.type || "audio/mpeg", size: file.size }),
      }));
      progress.hidden = false;
      show("Uploading " + file.name + "...");
      await put(upload.upload_url, file);
      show("Transcribing and scoring...");
      const fields = new FormData();
      fields.append("storage_path", upload.storage_path);
      for (const name of ["rubric", "language", "keyterms"]) fields.append(name, form[name].value);
      const call = await json(await fetch("/calls", { method: "POST", body: fields, credentials: "same-origin" }));
      window.location.href = "/dashboard/calls/" + encodeURIComponent(call.id);
    } catch (err) {
      show("Upload failed: " + err.message, "fail");
      button.disabled = false;
    }
  });
})();
