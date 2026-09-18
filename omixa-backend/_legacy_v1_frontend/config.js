// Where the frontend sends its API calls.
//
// Leave this as "" when the frontend and backend are served from the
// SAME place (e.g. running `python app.py` locally, or a single
// Render/Railway deploy that serves both static/ and the API).
//
// Set it to the backend's URL when the frontend is hosted separately
// from the backend — e.g. this frontend on Netlify, backend on
// Render/Railway. Example:
//
//   window.OMIXA_API_BASE = "https://omixa-backend.onrender.com";
//
window.OMIXA_API_BASE = "";

// If the backend has OMIXA_API_KEY set (see README's Security
// section), set the same value here so the frontend can send it
// back as the X-API-Key header. Leave as "" when the backend has no
// API key configured (the default, local-dev-friendly setup).
window.OMIXA_API_KEY = "";
