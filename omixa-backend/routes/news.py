"""Data & Analytics News: JSON API for the homepage carousel/news
page, plus the dedicated /news page itself.

The frontend never talks to the external news API directly, no key
ever ships to the browser. It only ever calls GET /api/news here,
which returns already-filtered, already-normalized JSON (or a status
of "unavailable" if the upstream API/key isn't working), see
news/service.py for the fetch/filter/cache logic itself.
"""

from flask import Blueprint, jsonify, render_template, request

from news.service import get_articles

news_bp = Blueprint("news", __name__)

# (value, label) pairs for the filter tabs on both the homepage
# carousel (not shown there, just "View all news") and /news.
CATEGORIES = [
    ("all", "All"),
    ("data", "Data"),
    ("analytics", "Analytics"),
    ("ai", "AI"),
    ("health", "Health Data"),
    ("technology", "Technology"),
]
VALID_CATEGORY_VALUES = {value for value, _ in CATEGORIES}


@news_bp.get("/api/news")
def api_news():
    category = request.args.get("category", "all")
    if category not in VALID_CATEGORY_VALUES:
        category = "all"

    articles, status = get_articles(category=category)
    return jsonify({"status": status, "category": category, "articles": articles})


@news_bp.get("/news")
def news_page():
    return render_template("news.html", categories=CATEGORIES)
