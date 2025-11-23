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
        raise RuntimeError(f"{FAL_KEY_ENV} environment variable is not set")
    return key


def get_fal_base() -> str:
    base = os.environ.get(FAL_BASE_ENV, DEFAULT_FAL_BASE)
    return base.rstrip("/")


def build_fal_url(path: str) -> str:
    base = get_fal_base()
    return f"{base}/{path.lstrip('/')}"


def build_forward_headers(incoming_headers) -> dict:
    """Prépare les headers pour le proxy générique /fal/..."""
    headers = {}
    for k, v in incoming_headers.items():
        lk = k.lower()
        if lk in ("host", "authorization", "content-length", "connection"):
            continue
        headers[k] = v

    headers["Authorization"] = f"Key {get_fal_key()}"
    return headers


def forward_to_fal(method: str, path: str, args, headers, body: bytes) -> Response:
    """Proxy brut vers Fal (texte -> image, etc.)."""
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
    response_headers = [
        (name, value)
        for name, value in resp.headers.items()
        if name.lower() not in excluded
    ]

    return Response(
        resp.content,
        status=resp.status_code,
        headers=response_headers,
    )


def run_image_edit(model_id: str, prompt: str, file_storage, extra_args=None) -> dict:
    """
    Image -> image avec Seedream / Nano Banana :
    - upload de l'image vers fal.media
    - appel du modèle avec image_urls + prompt
    """
    # Configure le client officiel Fal
    fal_client.api_key = get_fal_key()

    # Sauvegarde temporaire du fichier uploadé
    suffix = os.path.splitext(file_storage.filename or "")[1] or ".png"
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_name = tmp_file.name
    try:
        file_storage.save(tmp_name)
        # Upload vers fal.media -> retourne une URL publique
        image_url = fal_client.upload_file(tmp_name)
    finally:
        tmp_file.close()
        try:
            os.remove(tmp_name)
        except OSError:
            pass

    # Les endpoints "edit" attendent image_urls (liste)
    arguments = {"prompt": prompt, "image_urls": [image_url]}
    if extra_args:
        arguments.update(extra_args)

    # Appel synchro : fal_client.run gère le polling si besoin
    result = fal_client.run(model_id, arguments=arguments)
    return result


app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    # UI complète : texte->image + image->image
    return '''
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Fal Proxy UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 1.5rem; max-width: 960px; }
    label { display: block; margin-top: 0.75rem; font-weight: 600; }
    input[type="text"], textarea, select { width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }
    textarea { min-height: 140px; font-family: monospace; }
    button { margin-top: 0.75rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    #status, #editStatus { margin-top: 0.5rem; font-size: 0.9rem; }
    #images img, #editImages img { max-width: 100%; margin-top: 0.5rem; display: block; }
    pre { background: #111; color: #0f0; padding: 0.75rem; overflow-x: auto; font-size: 0.85rem; }
    .row { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 1.5rem; }
    .row > div { flex: 1 1 260px; }
    h1 { margin-bottom: 0.5rem; }
    h2 { margin-top: 1.5rem; margin-bottom: 0.25rem; }
    small { color: #555; }
  </style>
</head>
<body>
  <h1>Fal Proxy – Interface</h1>
  <p>
    Cette page parle à votre proxy Render, qui lui-même appelle Fal.ai avec votre clé.
  </p>

  <h2>1. Texte → Image (JSON direct)</h2>
  <small>Utilise le proxy générique <code>/fal/&lt;path&gt;</code>. Compatible avec Flux, Seedream texte, Nano Banana texte, etc.</small>

  <form id="falForm">
    <label>
      Modèle / chemin Fal (path)
      <input type="text" id="modelPath" value="fal-ai/flux/dev" required>
    </label>

    <label>
      Paramètres JSON à envoyer
      <textarea id="jsonInput">
{
  "prompt": "A cinematic portrait of a jazz pianist in warm light, ultra-detailed",
  "num_images": 1,
  "image_size": "landscape_4_3"
}
      </textarea>
    </label>

    <button type="submit">Envoyer (texte → image)</button>
    <div id="status"></div>
  </form>

  <div class="row">
    <div>
      <h3>Images détectées</h3>
      <div id="images"></div>
    </div>
    <div>
      <h3>Réponse brute</h3>
      <pre id="rawOutput"></pre>
    </div>
  </div>

  <h2>2. Image → Image (Seedream / Nano Banana)</h2>
  <small>
    Upload d'une image, sélection du modèle d'édition, et prompt de modification.
    Le serveur utilise le client Python officiel <code>fal_client</code>.
  </small>

  <form id="editForm" enctype="multipart/form-data">
    <label>
      Modèle d'édition
      <select id="editModel">
        <option value="fal-ai/nano-banana/edit">Nano Banana – Image Editing</option>
        <option value="fal-ai/bytedance/seedream/v4/edit">Seedream v4 – Image Editing</option>
      </select>
    </label>

    <label>
      Image à éditer
      <input type="file" id="editImage" name="image" accept="image/*" required>
    </label>

    <label>
      Prompt d'édition
      <textarea id="editPrompt">
make a cinematic portrait, soften the background, keep the face and lighting realistic
      </textarea>
    </label>

    <label>
      Options avancées (JSON, optionnel, fusionné dans les arguments)
      <textarea id="editExtraJson">
{
  "image_size": "auto"
}
      </textarea>
    </label>

    <button type="submit">Envoyer (image → image)</button>
    <div id="editStatus"></div>
  </form>

  <div class="row">
    <div>
      <h3>Images éditées</h3>
      <div id="editImages"></div>
    </div>
    <div>
      <h3>Réponse brute (édition)</h3>
      <pre id="editRawOutput"></pre>
    </div>
  </div>

  <script>
    // --- Texte -> Image ---
    const form = document.getElementById('falForm');
    const modelPathInput = document.getElementById('modelPath');
    const jsonInput = document.getElementById('jsonInput');
    const statusEl = document.getElementById('status');
    const imagesEl = document.getElementById('images');
    const rawOutputEl = document.getElementById('rawOutput');

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      statusEl.textContent = 'Envoi en cours...';
      imagesEl.innerHTML = '';
      rawOutputEl.textContent = '';

      let payload;
      try {
        payload = JSON.parse(jsonInput.value);
      } catch (err) {
        statusEl.textContent = 'Erreur: JSON invalide (' + err.message + ')';
        return;
      }

      let modelPath = modelPathInput.value.trim();
      while (modelPath.startsWith('/')) {
        modelPath = modelPath.slice(1);
      }
      if (!modelPath) {
        statusEl.textContent = 'Erreur: chemin de modèle vide.';
        return;
      }

      try {
        const resp = await fetch('/fal/' + modelPath, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify(payload)
        });

        const text = await resp.text();
        let data = null;
        try {
          data = JSON.parse(text);
        } catch (_) {
          data = null;
        }

        rawOutputEl.textContent = text;
        statusEl.textContent = 'Statut HTTP: ' + resp.status;

        if (data && data.images && Array.isArray(data.images)) {
          data.images.forEach((img) => {
            const url = img.url || img.image_url || img.href;
            if (url) {
              const imageEl = document.createElement('img');
              imageEl.src = url;
              imagesEl.appendChild(imageEl);
            }
          });
        }
      } catch (err) {
        statusEl.textContent = 'Erreur de requête: ' + err.message;
      }
    });

    // --- Image -> Image ---
    const editForm = document.getElementById('editForm');
    const editModel = document.getElementById('editModel');
    const editImage = document.getElementById('editImage');
    const editPrompt = document.getElementById('editPrompt');
    const editExtraJson = document.getElementById('editExtraJson');
    const editStatus = document.getElementById('editStatus');
    const editImagesEl = document.getElementById('editImages');
    const editRawOutputEl = document.getElementById('editRawOutput');

    editForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      editStatus.textContent = 'Envoi en cours...';
      editImagesEl.innerHTML = '';
      editRawOutputEl.textContent = '';

      if (!editImage.files || editImage.files.length === 0) {
        editStatus.textContent = 'Veuillez sélectionner une image.';
        return;
      }

      let extra = {};
      if (editExtraJson.value.trim()) {
        try {
          extra = JSON.parse(editExtraJson.value);
        } catch (err) {
          editStatus.textContent = 'JSON avancé invalide: ' + err.message;
          return;
        }
      }

      const fd = new FormData();
      fd.append('model', editModel.value);
      fd.append('prompt', editPrompt.value);
      fd.append('image', editImage.files[0]);
      fd.append('extra_json', JSON.stringify(extra));

      try {
        const resp = await fetch('/ui/edit', {
          method: 'POST',
          body: fd
        });

        const text = await resp.text();
        let data = null;
        try {
          data = JSON.parse(text);
        } catch (_) {
          data = null;
        }

        editRawOutputEl.textContent = text;
        editStatus.textContent = 'Statut HTTP: ' + resp.status;

        if (data && data.images && Array.isArray(data.images)) {
          data.images.forEach((img) => {
            const url = img.url || img.image_url || img.href;
            if (url) {
              const imageEl = document.createElement('img');
              imageEl.src = url;
              editImagesEl.appendChild(imageEl);
            }
          });
        }
      } catch (err) {
        editStatus.textContent = 'Erreur de requête: ' + err.message;
      }
    });
  </script>
</body>
</html>
    '''


@app.route("/fal/<path:fal_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def fal_proxy(fal_path: str):
    """Proxy brut pour tous les endpoints Fal (texte->image, etc.)."""
    body = request.get_data()
    return forward_to_fal(
        method=request.method,
        path=fal_path,
        args=request.args,
        headers=request.headers,
        body=body,
    )


@app.route("/ui/edit", methods=["POST"])
def ui_edit():
    """Endpoint spécifique pour l'édition d'image (Seedream / Nano Banana)."""
    model_id = request.form.get("model") or ""
    prompt = request.form.get("prompt") or ""
    extra_json = request.form.get("extra_json") or ""
    file_storage = request.files.get("image")

    if not model_id:
        return jsonify({"error": "missing_model"}), 400
    if not prompt:
        return jsonify({"error": "missing_prompt"}), 400
    if not file_storage:
        return jsonify({"error": "missing_image"}), 400

    extra_args = None
    if extra_json:
        try:
            extra_args = json.loads(extra_json)
            if not isinstance(extra_args, dict):
                extra_args = None
        except Exception:
            extra_args = None

    try:
        result = run_image_edit(
            model_id=model_id,
            prompt=prompt,
            file_storage=file_storage,
            extra_args=extra_args,
        )
    except Exception as e:
        return jsonify({"error": "edit_failed", "detail": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
