from flask import Blueprint, request, jsonify

from utils.file_handler import allowed_file, create_job_dir, save_upload, sweep_expired_jobs

upload_bp = Blueprint("upload", __name__)


@upload_bp.post("/")
def upload_file():
    """
    Frontend contract:

    Request:  multipart/form-data, field name "file"
    Response: { "job_id": "...", "filename": "patients.xlsx", "status": "uploaded" }
              or { "error": "..." }, 400
    """
    sweep_expired_jobs()  # V1 has no scheduler; opportunistically clear abandoned jobs on any traffic

    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Unsupported file type. Use CSV or Excel."}), 400

    job_id = create_job_dir()
    try:
        save_upload(file, job_id)
    except OSError:
        return jsonify({"error": "Could not save the uploaded file. Please try again."}), 500

    return jsonify({
        "job_id": job_id,
        "filename": file.filename,
        "status": "uploaded",
    }), 201
