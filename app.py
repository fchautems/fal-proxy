import os
import json
import tempfile

from flask import Flask, request, Response, jsonify
import requests
import fal_client

FAL_KEY_ENV = "FAL_KEY"
FAL_BASE_ENV = "FAL_BASE_URL"

DEFAULT_FAL_BASE = "https://fal.run"
TIMEOUT_SECONDS = 300


def get_fal_key() -> str:
    key = os.environ.get(FAL_KEY_ENV)
    if not key:
        raise RuntimeError(f"{FAL_KEY_ENV} is missing")
    return key


def get_fal_base() -> str:
    base = os.environ.get(FAL_BASE_ENV, DEFAULT_FAL_BASE)
    return base.rstrip("/")


def build_fal_url(path: str) -> str:
    return f"{get_fal_base()}/{path.lstrip('/')}"


def build_forward_headers(incoming_headers) -> dict:
    headers = {}
    for k, v in incoming_headers.items():
        lk = k.lower()
        if lk in ("host", "authorization", "content-length", "connection"):
            continue
        headers[k] = v
    headers["Authorization"] = f"Key {get_fal_key()}"
    return headers


def forward_to_fal(method: str, path: str, args, headers, body: bytes) -> Response:
    url = build_fal_url(path)
    params = {k: v for k, v in args.lists()}
    fal_headers = build_forward_headers(headers)

    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=fal_headers,
            params=params,
            data=body,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as e:
        return jsonify({"error": "request_to_fal_failed", "detail": str(e)}), 502

    excluded = {"content-encoding", "transfer-encoding", "connection"}
    clean_headers = [
        (name, value)
        for name, value in resp.headers.items()
        if name.lower() not in excluded
    ]

    return Response(resp.content, status=resp.status_code, headers=clean_headers)


def run_image_edit(model_id: str, prompt: str, files, extra_args=None) -> dict:
    # Upload toutes les images vers Fal et appelle le modèle avec image_urls = [...]
    fal_client.api_key = get_fal_key()
    image_urls = []

    for file_storage in files:
        if not file_storage or not file_storage.filename:
            continue

        suffix = os.path.splitext(file_storage.filename)[1] or ".png"
        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_name = tmp_file.name
        tmp_file.close()

        file_storage.save(tmp_name)
        try:
            url = fal_client.upload_file(tmp_name)
            image_urls.append(url)
        finally:
            try:
                os.remove(tmp_name)
            except OSError:
                pass

    if not image_urls:
        raise RuntimeError("No valid images were uploaded")

    arguments = {
        "prompt": prompt,
        "image_urls": image_urls,
    }

    if extra_args and isinstance(extra_args, dict):
        arguments.update(extra_args)

    result = fal_client.run(model_id, arguments=arguments)

    if isinstance(result, dict):
        result["_debug_image_urls"] = image_urls

    return result


app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return '''
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Fal Proxy UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: system-ui, sans-serif; margin: 1.5rem; max-width: 980px; }
    label { display: block; margin-top: 0.75rem; font-weight: 600; }
    input[type="text"], textarea, select { width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }
    textarea { min-height: 140px; font-family: monospace; }
    button { margin-top: 0.75rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    img { border-radius: 4px; }
    #previewContainer img { max-width: 110px; margin-right: 10px; border:1px solid #ccc; padding:3px; }
    pre { background: #111; color:#0f0; padding:0.75rem; white-space: pre-wrap; }
    .row{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem;}
    .row>div{flex:1 1 260px;}
    .hint { font-size: 0.85rem; color:#555; }
    .inline { display:flex; gap:0.5rem; align-items:center; flex-wrap:wrap; }
    .inline > * { flex: 0 0 auto; }
  </style>
</head>

<body>

<h1>Fal Proxy – Interface</h1>

<h2>1. Texte → Image (proxy générique)</h2>

<form id="falForm">
  <label>Modèle / chemin Fal
    <input type="text" id="modelPath" value="fal-ai/flux/dev">
  </label>

  <label>JSON
    <textarea id="jsonInput">
{
  "prompt": "beautiful cinematic portrait",
  "num_images": 1
}
    </textarea>
  </label>

  <button type="submit">Générer (Texte → Image)</button>
  <div id="status"></div>
</form>

<div class="row">
  <div>
    <h3>Images</h3>
    <div id="images"></div>
  </div>
  <div>
    <h3>Réponse brute</h3>
    <pre id="rawOutput"></pre>
  </div>
</div>


<h2>2. Image → Image (Seedream / Nano-Banana Pro)</h2>

<form id="editForm" enctype="multipart/form-data">

  <label>Modèle d'édition
    <select id="editModel">
      <option value="fal-ai/bytedance/seedream/v4/edit" selected>Seedream v4 – Edit</option>
      <option value="fal-ai/nano-banana-pro/edit">Nano-Banana Pro – Edit</option>
    </select>
  </label>

  <div id="modelOptions" class="hint">
  </div>

  <label>Images (multiple autorisé)
    <input type="file" id="editImages" accept="image/*" multiple>
  </label>

  <div id="previewContainer"></div>

  <label>Prompt
    <textarea id="editPrompt"></textarea>
  </label>

  <label>Options avancées (JSON fusionné)
    <textarea id="editExtraJson">
{
  "enable_safety_checker": false
}
    </textarea>
    <div class="hint">
      Les paramètres ci-dessus seront fusionnés avec ceux choisis dans les menus (image_size ou aspect_ratio / resolution).
    </div>
  </label>

  <button type="submit">Générer (Image → Image)</button>
  <div id="editStatus"></div>

</form>

<div class="row">
  <div>
    <h3>Images éditées</h3>
    <div id="editImagesOutput"></div>
  </div>
  <div>
    <h3>Réponse brute (édition)</h3>
    <pre id="editRawOutput"></pre>
  </div>
</div>

<script>
// --- Preview des images ---
const inputImgs = document.getElementById("editImages");
const preview = document.getElementById("previewContainer");

inputImgs.addEventListener("change", () => {
  preview.innerHTML = "";
  [...inputImgs.files].forEach(file => {
    const img = document.createElement("img");
    img.src = URL.createObjectURL(file);
    preview.appendChild(img);
  });
});

// --- Texte -> Image ---
document.getElementById("falForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const modelPathInput = document.getElementById("modelPath");
  const rawJson = document.getElementById("jsonInput").value;
  const status = document.getElementById("status");
  const imagesDiv = document.getElementById("images");
  const rawOut = document.getElementById("rawOutput");

  imagesDiv.innerHTML = "";
  rawOut.textContent = "";
  status.textContent = "Envoi...";

  let modelPath = modelPathInput.value.trim();
  while (modelPath.startsWith("/")) {
    modelPath = modelPath.slice(1);
  }

  let payload;
  try {
    payload = JSON.parse(rawJson);
  } catch (e2) {
    status.textContent = "JSON invalide : " + e2.message;
    return;
  }

  try {
    const resp = await fetch("/fal/" + modelPath, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const text = await resp.text();
    rawOut.textContent = text;
    status.textContent = "HTTP " + resp.status;

    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (data && Array.isArray(data.images)) {
      data.images.forEach(img => {
        const url = img.url || img.image_url;
        if (url) {
          const im = document.createElement("img");
          im.src = url;
          im.style.maxWidth = "100%";
          imagesDiv.appendChild(im);
        }
      });
    }
  } catch (err) {
    status.textContent = "Erreur : " + err.message;
  }
});

// --- UI dynamique selon le modèle ---
const editModel = document.getElementById("editModel");
const modelOptionsDiv = document.getElementById("modelOptions");

function renderModelOptions() {
  const model = editModel.value;
  let html = "";

  if (model === "fal-ai/bytedance/seedream/v4/edit") {
    html += '<div class="inline">';
    html += '<span>Taille d\\'image (Seedream) :</span>';
    html += '<select id="seedreamSize">';
    html += '<option value="">(défaut)</option>';
    html += '<option value="auto">auto</option>';
    html += '<option value="auto_2k">auto_2k</option>';
    html += '<option value="auto_4k">auto_4k</option>';
    html += '<option value="square">square</option>';
    html += '<option value="square_hd">square_hd</option>';
    html += '<option value="portrait_3_4">portrait_3_4</option>';
    html += '<option value="portrait_9_16">portrait_9_16</option>';
    html += '<option value="landscape_4_3">landscape_4_3</option>';
    html += '<option value="landscape_16_9">landscape_16_9</option>';
    html += '<option value="custom">custom (width/height)</option>';
    html += '</select>';
    html += '<span id="seedreamCustom" style="display:none;"> ';
    html += 'W:<input type="number" id="seedreamW" style="width:80px" min="256" step="64"> ';
    html += 'H:<input type="number" id="seedreamH" style="width:80px" min="256" step="64">';
    html += '</span>';
    html += '</div>';
    html += '<div class="hint">Seedream : utilise image_size ou width/height pour custom.</div>';
  } else if (model === "fal-ai/nano-banana-pro/edit") {
    html += '<div class="inline">';
    html += '<span>Aspect ratio :</span>';
    html += '<select id="nanoAspect">';
    html += '<option value="auto">auto</option>';
    html += '<option value="21:9">21:9</option>';
    html += '<option value="16:9">16:9</option>';
    html += '<option value="3:2">3:2</option>';
    html += '<option value="4:3">4:3</option>';
    html += '<option value="5:4">5:4</option>';
    html += '<option value="1:1">1:1</option>';
    html += '<option value="4:5">4:5</option>';
    html += '<option value="3:4">3:4</option>';
    html += '<option value="2:3">2:3</option>';
    html += '<option value="9:16">9:16</option>';
    html += '</select>';
    html += '</div>';

    html += '<div class="inline">';
    html += '<span>Resolution :</span>';
    html += '<select id="nanoRes">';
    html += '<option value="">(défaut)</option>';
    html += '<option value="1K">1K</option>';
    html += '<option value="2K">2K</option>';
    html += '<option value="4K">4K</option>';
    html += '</select>';
    html += '</div>';

    html += '<div class="hint">Nano-Banana Pro : aspect_ratio + resolution (1K/2K/4K).</div>';
  }

  modelOptionsDiv.innerHTML = html;

  const seedreamSize = document.getElementById("seedreamSize");
  const seedreamCustom = document.getElementById("seedreamCustom");
  if (seedreamSize && seedreamCustom) {
    seedreamSize.addEventListener("change", () => {
      if (seedreamSize.value === "custom") {
        seedreamCustom.style.display = "inline-flex";
      } else {
        seedreamCustom.style.display = "none";
      }
    });
  }
}

editModel.addEventListener("change", renderModelOptions);
renderModelOptions();

// --- Image -> Image submit ---
document.getElementById("editForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  const files = [...inputImgs.files];
  const editStatus = document.getElementById("editStatus");
  const out = document.getElementById("editImagesOutput");
  const raw = document.getElementById("editRawOutput");
  const model = editModel.value;

  out.innerHTML = "";
  raw.textContent = "";
  if (!files.length) {
    editStatus.textContent = "Aucune image.";
    return;
  }

  let extra = {};
  try {
    extra = JSON.parse(document.getElementById("editExtraJson").value || "{}");
  } catch (e2) {
    editStatus.textContent = "JSON options invalide : " + e2.message;
    return;
  }

  // Options spécifiques au modèle
  if (model === "fal-ai/bytedance/seedream/v4/edit") {
    const seedreamSize = document.getElementById("seedreamSize");
    if (seedreamSize) {
      const val = seedreamSize.value;
      delete extra["image_size"];
      delete extra["width"];
      delete extra["height"];

      if (val === "custom") {
        const w = parseInt(document.getElementById("seedreamW").value || "0", 10);
        const h = parseInt(document.getElementById("seedreamH").value || "0", 10);
        if (w > 0 && h > 0) {
          extra["width"] = w;
          extra["height"] = h;
        }
      } else if (val) {
        extra["image_size"] = val;
      }
    }
    delete extra["aspect_ratio"];
    delete extra["resolution"];
  } else if (model === "fal-ai/nano-banana-pro/edit") {
    const nanoAspect = document.getElementById("nanoAspect");
    const nanoRes = document.getElementById("nanoRes");

    delete extra["image_size"];
    delete extra["width"];
    delete extra["height"];

    if (nanoAspect && nanoAspect.value) {
      extra["aspect_ratio"] = nanoAspect.value;
    }
    if (nanoRes && nanoRes.value) {
      extra["resolution"] = nanoRes.value;
    }
  }

  const fd = new FormData();
  fd.append("model", model);
  fd.append("prompt", document.getElementById("editPrompt").value || "");
  fd.append("extra_json", JSON.stringify(extra));
  files.forEach(f => fd.append("images", f));

  editStatus.textContent = "Envoi...";

  try {
    const resp = await fetch("/ui/edit", { method: "POST", body: fd });
    const text = await resp.text();
    raw.textContent = text;
    editStatus.textContent = "HTTP " + resp.status;

    let data = null;
    try { data = JSON.parse(text); } catch (_) {}

    if (data && Array.isArray(data.images)) {
      data.images.forEach(img => {
        const url = img.url || img.image_url;
        if (url) {
          const im = document.createElement("img");
          im.src = url;
          im.style.maxWidth = "100%";
          out.appendChild(im);
        }
      });
    }
  } catch (err) {
    editStatus.textContent = "Erreur : " + err.message;
  }
});
</script>

</body>
</html>
'''


@app.route("/fal/<path:fal_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def fal_proxy(fal_path):
    return forward_to_fal(
        request.method,
        fal_path,
        request.args,
        request.headers,
        request.get_data()
    )


@app.route("/ui/edit", methods=["POST"])
def ui_edit():
    model = request.form.get("model")
    prompt = request.form.get("prompt") or ""
    extra_json = request.form.get("extra_json") or "{}"
    files = request.files.getlist("images")

    if not files:
        return jsonify({"error": "missing_images"}), 400
    if not model:
        return jsonify({"error": "missing_model"}), 400

    try:
        extra = json.loads(extra_json)
    except Exception:
        extra = {}

    try:
        result = run_image_edit(model, prompt, files, extra)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error":"edit_failed","detail":str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000, debug=True)
