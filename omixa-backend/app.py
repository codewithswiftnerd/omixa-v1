"""
Omixa Backend, Entry Point

Plain V1 shape: no accounts, no database, no billing. Every request
is one job, backed by a temp folder that gets deleted after download
(or after 30 min if abandoned). See routes/upload.py, process.py,
download.py, report.py for the API, and processing/ + cleaning/ for
the engine itself.

Page routes (landing / pricing / clean workspace) are server-rendered
Jinja2 templates styled by static/css/app.css. No React or other
frontend framework.
"""

import os
import logging
from flask import Flask, jsonify, render_template, request, send_from_directory
from flask_cors import CORS

from config import Config

from routes.upload import upload_bp
from routes.process import process_bp
from routes.download import download_bp
from routes.report import report_bp
from routes.pages import pages_bp
from routes.workspace import workspace_bp
from utils.security import check_api_key, check_rate_limit


def create_app():
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_object(Config)

    # expose_headers: the frontend reads Content-Disposition to name
    # downloads, and Retry-After for rate-limit 429s, both are hidden
    # cross-origin by default.
    origins = Config.ALLOWED_ORIGINS
    origins = "*" if origins == "*" else [o.strip() for o in origins.split(",") if o.strip()]
    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        expose_headers=["Content-Disposition", "Retry-After"],
    )

    # API blueprints (JSON, unchanged contracts)
    app.register_blueprint(upload_bp, url_prefix="/api/upload")
    app.register_blueprint(process_bp, url_prefix="/api/process")
    app.register_blueprint(download_bp, url_prefix="/api/download")
    app.register_blueprint(report_bp, url_prefix="/api/report")

    # Page blueprints (server-rendered HTML)
    app.register_blueprint(pages_bp)
    app.register_blueprint(workspace_bp)

    @app.before_request
    def _security_gate():
        # Rate limit first, then API key. Both no-ops unless
        # configured (config.py). /api/* only, /api/health exempt.
        limited = check_rate_limit()
        if limited:
            return limited
        return check_api_key()

    def _wants_json() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(413)
    def too_large(e):
        mb = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
        message = f"File too large, the limit is {mb}MB."
        if _wants_json():
            return jsonify({"error": message}), 413
        return render_template("errors/error.html", code=413, message=message), 413

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify({"error": "Not found"}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        logging.exception("Unhandled server error")
        if _wants_json():
            return jsonify({"error": "Something went wrong on our end. Please try again."}), 500
        return render_template(
            "errors/error.html", code=500, message="Something went wrong on our end. Please try again."
        ), 500

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "omixa-backend"}

    @app.get("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "favicon.ico")

    return app


app = create_app()

if __name__ == "__main__":
    # Never turn debug on in production, it enables remote code
    # execution via Werkzeug's debugger. python app.py is local dev
    # only; production runs gunicorn (see Procfile).
    debug = os.environ.get("FLASK_DEBUG") == "1"
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug, port=port)
