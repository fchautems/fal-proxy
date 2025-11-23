import os
from flask import Flask, request, Response, jsonify
import requests

# Noms des variables d'environnement
FAL_KEY_ENV = "FAL_KEY"        # ta clé Fal
FAL_BASE_ENV = "FAL_BASE_URL"  # optionnel, pour choisir fal.run ou queue.fal.run

# Base Fal par défaut : synchro
DEFAULT_FAL_BASE = "https://fal.run"
TIMEOUT_SECONDS = 300


def get_fal_key() -> str:
    """Lit la clé Fal dans l'environnement ou lève une erreur claire."""
    key = os.environ.get(FAL_KEY_ENV)
    if not key:
        raise RuntimeError(f"{FAL_KEY_ENV} environment variable is not set")
    return key


def get_fal_base() -> str:
    """Retourne l'URL de base Fal, sans slash final."""
    base = os.environ.get(FAL_BASE_ENV, DEFAULT_FAL_BASE)
    return base.rstrip("/")


def build_fal_url(path: str) -> str:
    """Construit l'URL Fal complète à partir d'un chemin 'fal-ai/flux/dev'."""
    base = get_fal_base()
    return f"{base}/{path.lstrip('/')}"


def build_forward_headers(incoming_headers) -> dict:
    """Copie les headers utiles, enlève ceux qui posent problème, ajoute l'auth Fal."""
    headers = {}
    for k, v in incoming_headers.items():
        lk = k.lower()
        if lk in ("host", "authorization", "content-length", "connection"):
            continue
        headers[k] = v

    headers["Authorization"] = f"Key {get_fal_key()}"
    return headers


def forward_to_fal(method: str, path: str, args, headers, body: bytes) -> Response:
    """
    Forward générique vers Fal.
    - method : GET/POST/...
    - path   : 'fal-ai/flux/dev' ou 'fal-ai/veo3/fast', etc.
    """
    url = build_fal_url(path)
    # Support des paramètres multiples: ?foo=a&foo=b
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
        # Réponse JSON propre en cas d'erreur réseau
        return jsonify({"error": "request_to_fal_failed", "detail": str(e)}), 502

    # On filtre quelques headers qui posent problème en proxy
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
def root():
    """Endpoint de santé simple."""
    return jsonify(
        {
            "status": "ok",
            "message": "Fal proxy running",
            "fal_base": get_fal_base(),
        }
    )


@app.route("/fal/<path:fal_path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def fal_proxy(fal_path: str):
    """
    Proxy générique :
    /fal/fal-ai/flux/dev   -> https://fal.run/fal-ai/flux/dev      (par défaut)
    /fal/fal-ai/veo3/fast  -> https://queue.fal.run/fal-ai/veo3/fast (si FAL_BASE_URL=queue.fal.run)
    """
    body = request.get_data()  # JSON, multipart, binaire... tout passe
    return forward_to_fal(
        method=request.method,
        path=fal_path,
        args=request.args,
        headers=request.headers,
        body=body,
    )


if __name__ == "__main__":
    # Pour tests en local éventuels
    app.run(host="0.0.0.0", port=5000, debug=True)
