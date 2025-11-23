import os
from flask import Flask, request, Response, jsonify
import requests

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
    body { font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif; margin: 1.5rem; max-width: 900px; }
    label { display: block; margin-top: 1rem; font-weight: 600; }
    input[type="text"], textarea { width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }
    textarea { min-height: 160px; font-family: monospace; }
    button { margin-top: 1rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    #status { margin-top: 1rem; font-size: 0.9rem; }
    #images img { max-width: 100%; margin-top: 0.5rem; display: block; }
    pre { background: #111; color: #0f0; padding: 0.75rem; overflow-x: auto; font-size: 0.85rem; }
    .row { display: flex; gap: 1rem; flex-wrap: wrap; }
    .row > div { flex: 1 1 250px; }
  </style>
</head>
<body>
  <h1>Fal Proxy – Interface simple</h1>
  <p>
    Cette page envoie les requêtes vers votre proxy Render, qui lui-même parle à Fal.ai.
    Vous pouvez changer le modèle et le JSON librement.
  </p>

  <form id="falForm">
    <label>
      Modèle / chemin Fal (path)
      <input type="text" id="modelPath" value="fal-ai/flux/dev" required>
    </label>

    <label>
      Paramètres JSON à envoyer (inclure au minimum <code>"prompt"</code>)
      <textarea id="jsonInput">
{
  "prompt": "A cinematic portrait of a jazz pianist in warm light, ultra-detailed",
  "num_images": 1,
  "image_size": "landscape_4_3"
}
      </textarea>
    </label>

    <button type="submit">Envoyer la requête</button>
    <div id="status"></div>
  </form>

  <div class="row">
    <div>
      <h2>Images détectées</h2>
      <div id="images"></div>
    </div>
    <div>
      <h2>Réponse brute</h2>
      <pre id="rawOutput"></pre>
    </div>
  </div>

  <script>
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

      const modelPath = modelPathInput.value.trim().replace(/^\\/+/, '');
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
  </script>
</body>
</html>
    '''


@app.route("/fal/<path:fal_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def fal_proxy(fal_path: str):
    body = request.get_data()
    return forward_to_fal(
        method=request.method,
        path=fal_path,
        args=request.args,
        headers=request.headers,
        body=body,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
