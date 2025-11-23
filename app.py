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
    fal_client.api_key = get_fal_key()
    image_urls = []

    for file_storage in files:
        suffix = os.path.splitext(file_storage.filename or "")[1] or ".png"
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
            except:
                pass

    arguments = {
        "prompt": prompt,
        "image_urls": image_urls
    }

    if extra_args:
        arguments.update(extra_args)

    result = fal_client.run(model_id, arguments=arguments)
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
    body { font-family: system-ui, sans-serif; margin: 1.5rem; max-width: 960px; }
    label { display: block; margin-top: 0.75rem; font-weight: 600; }
    input[type="text"], textarea, select { width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }
    textarea { min-height: 140px; font-family: monospace; }
    button { margin-top: 0.75rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    img { border-radius: 4px; }
    #previewContainer img { max-width: 110px; margin-right: 10px; border:1px solid #ccc; padding:3px; }
    pre { background: #111; color:#0f0; padding:0.75rem; white-space: pre-wrap; }
    .row{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem;}
    .row>div{flex:1 1 260px;}
  </style>
</head>

<body>

<h1>Fal Proxy – Interface complète</h1>

<h2>1. Texte → Image</h2>

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


<h2>2. Image → Image (Seedream / Nano-Banana)</h2>

<form id="editForm" enctype="multipart/form-data">

<label>Modèle d'édition
<select id="editModel">
  <option value="fal-ai/bytedance/seedream/v4/edit" selected>Seedream v4 – Edit</option>
  <option value="fal-ai/nano-banana/edit">Nano-Banana – Edit</option>
</select>
</label>

<label>Images (multiple autorisé)
  <input type="file" id="editImages" accept="image/*" multiple>
</label>

<div id="previewContainer"></div>

<label>Prompt
  <textarea id="editPrompt"></textarea>
</label>

<label>Options (JSON)
<textarea id="editExtraJson">
{
  "enable_safety_checker": false,
  "image_size": "auto"
}
</textarea>
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
    <h3>Réponse brute</h3>
    <pre id="editRawOutput"></pre>
  </div>
</div>


<script>
// --- Preview ---
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
document.getElementById("falForm").addEventListener("submit", async e => {
  e.preventDefault();

  const modelPath = document.getElementById("modelPath").value.trim().replace(/^\\/+/, "");
  const rawJson = document.getElementById("jsonInput").value;
  const status = document.getElementById("status");
  const imagesDiv = document.getElementById("images");
  const rawOut = document.getElementById("rawOutput");

  imagesDiv.innerHTML = "";
  rawOut.textContent = "";
  status.textContent = "Envoi...";

  let payload;
  try { payload = JSON.parse(rawJson); }
  catch(e){ status.textContent="JSON invalide : "+e.message; return; }

  try {
    const resp = await fetch('/fal/' + modelPath, {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });
    const text = await resp.text();
    rawOut.textContent = text;
    status.textContent = "HTTP " + resp.status;

    let data=null;
    try{ data = JSON.parse(text); } catch{}
    if(data && Array.isArray(data.images)){
      data.images.forEach(img=>{
        const url = img.url || img.image_url;
        if(url){
          const im = document.createElement("img");
          im.src = url;
          im.style.maxWidth="100%";
          imagesDiv.appendChild(im);
        }
      });
    }
  } catch(err){
    status.textContent = "Erreur : "+err.message;
  }
});

// --- Image -> Image ---
document.getElementById("editForm").addEventListener("submit", async e => {
  e.preventDefault();

  const files = [...inputImgs.files];
  const editStatus = document.getElementById("editStatus");
  const out = document.getElementById("editImagesOutput");
  const raw = document.getElementById("editRawOutput");

  out.innerHTML=""; raw.textContent="";
  if(!files.length){ editStatus.textContent="Aucune image."; return; }

  let extra = {};
  try { extra = JSON.parse(document.getElementById("editExtraJson").value); }
  catch(e){ editStatus.textContent="JSON options invalide"; return; }

  const fd = new FormData();
  fd.append("model", document.getElementById("editModel").value);
  fd.append("prompt", document.getElementById("editPrompt").value);
  fd.append("extra_json", JSON.stringify(extra));
  files.forEach(f => fd.append("images", f));

  editStatus.textContent="Envoi...";

  try{
    const resp = await fetch("/ui/edit", { method:"POST", body:fd });
    const text=await resp.text();
    raw.textContent=text;
    editStatus.textContent="HTTP "+resp.status;

    let data=null; try{ data=JSON.parse(text);}catch{}
    if(data && Array.isArray(data.images)){
      data.images.forEach(img=>{
        const url = img.url || img.image_url;
        if(url){
          const im = document.createElement("img");
          im.src=url;
          im.style.maxWidth="100%";
          out.appendChild(im);
        }
      });
    }
  }catch(err){
    editStatus.textContent="Erreur : "+err.message;
  }
});
</script>

</body>
</html>
'''


@app.route("/fal/<path:fal_path>", methods=["GET","POST","PUT","DELETE","PATCH","OPTIONS"])
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
    except:
        extra = {}

    try:
        result = run_image_edit(model, prompt, files, extra)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error":"edit_failed","detail":str(e)}), 500


if __name__ == "__main__":
    app.run(port=5000, debug=True)
