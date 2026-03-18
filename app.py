import email.utils
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, Response, g, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "task_console.db"
CACHE_DIR = BASE_DIR / "cache"
YOUTUBE_CACHE_DIR = CACHE_DIR / "youtube"

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_cache():
    """Initialize cache directories"""
    if not CACHE_DIR.exists():
        CACHE_DIR.mkdir(parents=True)
    if not YOUTUBE_CACHE_DIR.exists():
        YOUTUBE_CACHE_DIR.mkdir(parents=True)


def init_db():
    init_cache()
    db = sqlite3.connect(DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            priority TEXT NOT NULL DEFAULT 'medium',
            due_date TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    task_columns = {row[1] for row in cursor.execute("PRAGMA table_info(tasks)").fetchall()}
    if "priority" not in task_columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
    if "due_date" not in task_columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    if "sort_order" not in task_columns:
        cursor.execute("ALTER TABLE tasks ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
    cursor.execute(
        """
        UPDATE tasks
        SET sort_order = id
        WHERE sort_order IS NULL OR sort_order = 0
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            content TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute("INSERT OR IGNORE INTO notes (id, content) VALUES (1, '')")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rss_feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS youtube_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url TEXT NOT NULL,
            channel_id TEXT NOT NULL UNIQUE,
            title TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO youtube_channels (source_url, channel_id, title)
        VALUES (?, ?, ?)
        """,
        (
            "https://www.youtube.com/@freecodecamp",
            "UC8butISFwT-Wl7EV0hUK0BQ",
            "freeCodeCamp",
        ),
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS podcast_feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            category TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS favorite_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL UNIQUE,
            channel_id TEXT,
            channel_title TEXT,
            title TEXT,
            link TEXT,
            thumbnail TEXT,
            published_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    db.commit()
    db.close()


def row_to_task(row):
    return {
        "id": row["id"],
        "text": row["text"],
        "done": bool(row["done"]),
        "priority": row["priority"],
        "due_date": row["due_date"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
    }


def parse_rfc822_date(value):
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def parse_iso_date(value):
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_feed_datetime(item):
    date_candidates = [
        item.findtext("pubDate"),
        item.findtext("{http://purl.org/dc/elements/1.1/}date"),
        item.findtext("{http://www.w3.org/2005/Atom}updated"),
        item.findtext("{http://www.w3.org/2005/Atom}published"),
        item.findtext("updated"),
        item.findtext("published"),
    ]
    for candidate in date_candidates:
        parsed = parse_rfc822_date(candidate) or parse_iso_date(candidate)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def text_or_default(value, fallback):
    return value.strip() if value and value.strip() else fallback


def fetch_url(url, accept_header):
    request_obj = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TaskConsoleFetcher/1.0 (+https://localhost)",
            "Accept": accept_header,
        },
    )
    with urllib.request.urlopen(request_obj, timeout=12) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
    return payload, content_type, charset


def fetch_feed_preview(url):
    payload, _, _ = fetch_url(
        url,
        "application/rss+xml, application/atom+xml, application/xml, text/xml",
    )
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is not None:
        title = text_or_default(channel.findtext("title"), url)
        item_nodes = channel.findall("item")
    else:
        title = text_or_default(root.findtext("{http://www.w3.org/2005/Atom}title"), url)
        item_nodes = root.findall("{http://www.w3.org/2005/Atom}entry")

    articles = []
    for item in item_nodes[:50]:
        if item.tag.endswith("entry"):
            link_node = item.find("{http://www.w3.org/2005/Atom}link")
            link = link_node.attrib.get("href", "") if link_node is not None else ""
            summary = item.findtext("{http://www.w3.org/2005/Atom}summary") or item.findtext(
                "{http://www.w3.org/2005/Atom}content"
            )
            article_title = item.findtext("{http://www.w3.org/2005/Atom}title")
        else:
            link = item.findtext("link", "")
            summary = item.findtext("description")
            article_title = item.findtext("title")

        articles.append(
            {
                "title": text_or_default(article_title, "Untitled article"),
                "link": link.strip(),
                "summary": (summary or "").strip(),
                "published_at": parse_feed_datetime(item).isoformat(),
            }
        )

    articles.sort(key=lambda article: article["published_at"], reverse=True)
    return {"title": title, "articles": articles}


def build_iframe_document(article_url):
    payload, content_type, charset = fetch_url(article_url, "text/html,application/xhtml+xml")
    body = payload.decode(charset, errors="replace")

    if "html" not in content_type:
        escaped = (
            body.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        return f"<pre>{escaped}</pre>"

    base_tag = f'<base href="{article_url}"><base target="_blank">'
    if "<head" in body.lower():
        split_marker = body.lower().find("<head")
        head_end = body.lower().find(">", split_marker)
        if head_end != -1:
            return body[: head_end + 1] + base_tag + body[head_end + 1 :]
    return f"<html><head>{base_tag}</head><body>{body}</body></html>"


def extract_youtube_channel_id(source_text):
    patterns = [
        r'"channelId":"(UC[\w-]+)"',
        r'"externalId":"(UC[\w-]+)"',
        r'"browseId":"(UC[\w-]+)"',
        r'https://www\.youtube\.com/feeds/videos\.xml\?channel_id=(UC[\w-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, source_text)
        if match:
            return match.group(1)
    return None


def extract_youtube_channel_title(source_text):
    patterns = [
        r'<meta property="og:title" content="([^"]+)"',
        r'<meta name="title" content="([^"]+)"',
        r'"title":"([^"]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, source_text)
        if match:
            return match.group(1).strip()
    return None


def resolve_youtube_channel(channel_url):
    parsed = urlparse(channel_url)
    normalized_url = channel_url.strip()
    if not normalized_url:
        raise ValueError("YouTube channel URL is required.")

    query = parse_qs(parsed.query)
    if "channel_id" in query and query["channel_id"]:
        channel_id = query["channel_id"][0]
        return {"channel_id": channel_id, "title": None, "source_url": normalized_url}

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] == "channel" and path_parts[1].startswith("UC"):
        return {"channel_id": path_parts[1], "title": None, "source_url": normalized_url}

    payload, _, charset = fetch_url(normalized_url, "text/html,application/xhtml+xml")
    body = payload.decode(charset, errors="replace")
    channel_id = extract_youtube_channel_id(body)
    if not channel_id:
        raise ValueError("Unable to resolve the YouTube channel ID from that link.")

    return {
        "channel_id": channel_id,
        "title": extract_youtube_channel_title(body),
        "source_url": normalized_url,
    }


def fetch_youtube_channel_videos(channel_id, use_cache=True, force_refresh=False):
    """Fetch YouTube channel videos with local cache support"""
    cache_file = YOUTUBE_CACHE_DIR / f"{channel_id}.json"
    cache_expiry = 3600 * 24  # 24 hours
    cache_stale = 3600 * 48  # 48 hours - after this we force refresh
    
    # Try to load from cache if enabled and not forcing refresh
    if use_cache and not force_refresh and cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            age = time.time() - mtime
            if age < cache_expiry:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                cached_data['_source'] = 'cache'
                return cached_data
            elif age < cache_stale:
                # Between 24-48 hours: use cache but mark as stale for potential refresh
                with open(cache_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                cached_data['_source'] = 'cache_stale'
                return cached_data
        except (json.JSONDecodeError, OSError):
            pass  # Fall back to fetching from network
    
    # Fetch from YouTube
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    payload, _, _ = fetch_url(feed_url, "application/atom+xml, application/xml, text/xml")
    root = ET.fromstring(payload)

    atom_ns = "{http://www.w3.org/2005/Atom}"
    yt_ns = "{http://www.youtube.com/xml/schemas/2015}"

    title = text_or_default(root.findtext(f"{atom_ns}title"), channel_id)
    author_name = root.findtext(f"{atom_ns}author/{atom_ns}name")
    entries = []

    for entry in root.findall(f"{atom_ns}entry")[:20]:
        video_id = entry.findtext(f"{yt_ns}videoId")
        link_node = entry.find(f"{atom_ns}link")
        link = link_node.attrib.get("href", "") if link_node is not None else ""
        entries.append(
            {
                "video_id": video_id,
                "title": text_or_default(entry.findtext(f"{atom_ns}title"), "Untitled video"),
                "link": link,
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else "",
                "published_at": parse_feed_datetime(entry).isoformat(),
                "channel_title": author_name or title,
            }
        )

    data = {"title": author_name or title, "videos": entries, "_source": "network"}
    
    # Save to cache
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Cache write failed, but we can still return data
    
    return data


def fetch_podcast_preview(url):
    payload, _, _ = fetch_url(
        url,
        "application/rss+xml, application/atom+xml, application/xml, text/xml",
    )
    root = ET.fromstring(payload)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Podcast feed must be an RSS feed with a channel element.")

    title = text_or_default(channel.findtext("title"), url)
    category = (
        channel.findtext("category")
        or channel.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}category")
        or "General"
    )

    episodes = []
    for item in channel.findall("item")[:50]:
        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url", "").strip() if enclosure is not None else ""
        if not audio_url:
            continue
        item_category = (
            item.findtext("category")
            or item.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}category")
            or category
        )
        episodes.append(
            {
                "title": text_or_default(item.findtext("title"), "Untitled episode"),
                "description": (item.findtext("description") or "").strip(),
                "audio_url": audio_url,
                "link": (item.findtext("link") or audio_url).strip(),
                "published_at": parse_feed_datetime(item).isoformat(),
                "category": text_or_default(item_category, category),
            }
        )

    episodes.sort(key=lambda episode: episode["published_at"], reverse=True)
    return {"title": title, "category": text_or_default(category, "General"), "episodes": episodes}


def normalize_terminal_cwd(cwd_value):
    candidate = (cwd_value or str(BASE_DIR)).strip()
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    else:
        path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("Working directory does not exist.")
    return path


def run_terminal_command(command, cwd_value):
    cwd = normalize_terminal_cwd(cwd_value)
    stripped = (command or "").strip()
    if not stripped:
        return {"stdout": "", "stderr": "", "exit_code": 0, "cwd": str(cwd)}

    if stripped.lower() in {"cls", "clear"}:
        return {"stdout": "", "stderr": "", "exit_code": 0, "cwd": str(cwd)}

    cd_match = re.match(r"^cd(?:\s+(.+))?$", stripped, flags=re.IGNORECASE)
    if cd_match:
        raw_target = (cd_match.group(1) or "").strip().strip('"')
        target = Path.home() if not raw_target else Path(raw_target)
        next_cwd = (cwd / target).resolve() if not target.is_absolute() else target.resolve()
        if not next_cwd.exists() or not next_cwd.is_dir():
            return {
                "stdout": "",
                "stderr": f"The system cannot find the path specified: {next_cwd}",
                "exit_code": 1,
                "cwd": str(cwd),
            }
        return {"stdout": "", "stderr": "", "exit_code": 0, "cwd": str(next_cwd)}

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            stripped,
        ],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
        shell=False,
        env=os.environ.copy(),
    )
    return {
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
        "cwd": str(cwd),
    }


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.get("/article-view")
def article_view():
    article_url = (request.args.get("url") or "").strip()
    if not article_url:
        return Response("Article URL is required.", status=400, mimetype="text/plain")

    try:
        document = build_iframe_document(article_url)
    except urllib.error.URLError as exc:
        return Response(f"Unable to load article: {exc}", status=400, mimetype="text/plain")

    return Response(document, mimetype="text/html")


@app.get("/api/tasks")
def list_tasks():
    rows = get_db().execute(
        """
        SELECT id, text, done, priority, due_date, sort_order, created_at
        FROM tasks
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()
    return jsonify([row_to_task(row) for row in rows])


@app.get("/api/tasks/next")
def list_next_tasks():
    limit = request.args.get("limit", default=1, type=int) or 1
    limit = max(1, min(limit, 10))
    rows = get_db().execute(
        """
        SELECT id, text, done, priority, due_date, sort_order, created_at
        FROM tasks
        WHERE done = 0
        ORDER BY
            CASE priority
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                ELSE 3
            END ASC,
            created_at ASC,
            id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return jsonify([row_to_task(row) for row in rows])


@app.post("/api/tasks")
def create_task():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    priority = (payload.get("priority") or "medium").strip().lower()
    due_date = (payload.get("due_date") or "").strip() or None
    if not text:
        return jsonify({"error": "Task text is required."}), 400
    if priority not in {"low", "medium", "high"}:
        return jsonify({"error": "Priority must be low, medium, or high."}), 400

    db = get_db()
    next_sort_order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tasks").fetchone()[0]
    cursor = db.execute(
        "INSERT INTO tasks (text, done, priority, due_date, sort_order) VALUES (?, 0, ?, ?, ?)",
        (text, priority, due_date, next_sort_order),
    )
    db.commit()
    row = db.execute(
        "SELECT id, text, done, priority, due_date, sort_order, created_at FROM tasks WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(row_to_task(row)), 201


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id):
    payload = request.get_json(silent=True) or {}
    updates = []
    values = []

    if "text" in payload:
        text = (payload.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Task text cannot be empty."}), 400
        updates.append("text = ?")
        values.append(text)

    if "done" in payload:
        updates.append("done = ?")
        values.append(1 if payload.get("done") else 0)

    if "priority" in payload:
        priority = (payload.get("priority") or "").strip().lower()
        if priority not in {"low", "medium", "high"}:
            return jsonify({"error": "Priority must be low, medium, or high."}), 400
        updates.append("priority = ?")
        values.append(priority)

    if "due_date" in payload:
        due_date = (payload.get("due_date") or "").strip() or None
        updates.append("due_date = ?")
        values.append(due_date)

    if "sort_order" in payload:
        try:
            sort_order = int(payload.get("sort_order"))
        except (TypeError, ValueError):
            return jsonify({"error": "sort_order must be an integer."}), 400
        updates.append("sort_order = ?")
        values.append(sort_order)

    if not updates:
        return jsonify({"error": "No valid task fields provided."}), 400

    values.append(task_id)
    db = get_db()
    cursor = db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Task not found."}), 404

    row = db.execute(
        "SELECT id, text, done, priority, due_date, sort_order, created_at FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return jsonify(row_to_task(row))


@app.post("/api/tasks/reorder")
def reorder_tasks():
    payload = request.get_json(silent=True) or {}
    task_ids = payload.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        return jsonify({"error": "task_ids must be a non-empty list."}), 400

    db = get_db()
    existing_ids = {
        row["id"]
        for row in db.execute("SELECT id FROM tasks WHERE id IN ({})".format(",".join("?" * len(task_ids))), task_ids).fetchall()
    }
    if existing_ids != set(task_ids):
        return jsonify({"error": "One or more task IDs were not found."}), 400

    for index, task_id in enumerate(task_ids, start=1):
        db.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (index, task_id))
    db.commit()
    rows = db.execute(
        """
        SELECT id, text, done, priority, due_date, sort_order, created_at
        FROM tasks
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()
    return jsonify([row_to_task(row) for row in rows])


@app.delete("/api/tasks/<int:task_id>")
def delete_task(task_id):
    db = get_db()
    cursor = db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Task not found."}), 404
    return ("", 204)


@app.get("/api/notes")
def get_notes():
    row = get_db().execute("SELECT content, updated_at FROM notes WHERE id = 1").fetchone()
    return jsonify({"content": row["content"], "updated_at": row["updated_at"]})


@app.put("/api/notes")
def save_notes():
    payload = request.get_json(silent=True) or {}
    content = payload.get("content", "")
    db = get_db()
    db.execute(
        """
        UPDATE notes
        SET content = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (content,),
    )
    db.commit()
    row = db.execute("SELECT content, updated_at FROM notes WHERE id = 1").fetchone()
    return jsonify({"content": row["content"], "updated_at": row["updated_at"]})


@app.get("/api/notes/saved")
def get_saved_notes():
    rows = get_db().execute(
        "SELECT id, content, created_at FROM saved_notes ORDER BY created_at DESC"
    ).fetchall()
    return jsonify(
        [
            {"id": row["id"], "content": row["content"], "created_at": row["created_at"]}
            for row in rows
        ]
    )


@app.post("/api/notes/saved")
def save_note():
    payload = request.get_json(silent=True) or {}
    content = (payload.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Note content is required."}), 400

    db = get_db()
    cursor = db.execute(
        "INSERT INTO saved_notes (content) VALUES (?)",
        (content,),
    )
    db.commit()
    row = db.execute(
        "SELECT id, content, created_at FROM saved_notes WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify({"id": row["id"], "content": row["content"], "created_at": row["created_at"]}), 201


@app.delete("/api/notes/saved/<int:note_id>")
def delete_saved_note(note_id):
    db = get_db()
    cursor = db.execute("DELETE FROM saved_notes WHERE id = ?", (note_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Note not found."}), 404
    return ("", 204)


@app.get("/api/rss/feeds")
def list_rss_feeds():
    rows = get_db().execute(
        "SELECT id, url, title, created_at FROM rss_feeds ORDER BY created_at DESC, id DESC"
    ).fetchall()
    return jsonify(
        [
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    )


@app.post("/api/rss/feeds")
def add_rss_feed():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "RSS feed URL is required."}), 400

    try:
        preview = fetch_feed_preview(url)
    except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
        return jsonify({"error": f"Unable to read RSS feed: {exc}"}), 400

    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO rss_feeds (url, title) VALUES (?, ?)",
            (url, preview["title"]),
        )
        db.commit()
    except sqlite3.IntegrityError:
        existing = db.execute(
            "SELECT id, url, title, created_at FROM rss_feeds WHERE url = ?",
            (url,),
        ).fetchone()
        return (
            jsonify(
                {
                    "id": existing["id"],
                    "url": existing["url"],
                    "title": existing["title"] or existing["url"],
                    "created_at": existing["created_at"],
                }
            ),
            200,
        )

    row = db.execute(
        "SELECT id, url, title, created_at FROM rss_feeds WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return (
        jsonify(
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "created_at": row["created_at"],
            }
        ),
        201,
    )


@app.delete("/api/rss/feeds/<int:feed_id>")
def delete_rss_feed(feed_id):
    db = get_db()
    cursor = db.execute("DELETE FROM rss_feeds WHERE id = ?", (feed_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Feed not found."}), 404
    return ("", 204)


@app.get("/api/rss/articles")
def list_rss_articles():
    db = get_db()
    feeds = db.execute(
        "SELECT id, url, title FROM rss_feeds ORDER BY created_at DESC, id DESC"
    ).fetchall()

    all_articles = []
    feed_errors = []

    for feed in feeds:
        try:
            preview = fetch_feed_preview(feed["url"])
        except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
            feed_errors.append(
                {
                    "feed_id": feed["id"],
                    "feed_title": feed["title"] or feed["url"],
                    "message": str(exc),
                }
            )
            continue

        if preview["title"] and preview["title"] != (feed["title"] or ""):
            db.execute("UPDATE rss_feeds SET title = ? WHERE id = ?", (preview["title"], feed["id"]))
            db.commit()

        for article in preview["articles"]:
            all_articles.append(
                {
                    "feed_id": feed["id"],
                    "feed_title": preview["title"],
                    "title": article["title"],
                    "link": article["link"],
                    "summary": article["summary"],
                    "published_at": article["published_at"],
                }
            )

    all_articles.sort(key=lambda article: article["published_at"], reverse=True)
    return jsonify({"articles": all_articles, "errors": feed_errors})


@app.get("/api/youtube/channels")
def list_youtube_channels():
    rows = get_db().execute(
        """
        SELECT id, source_url, channel_id, title, created_at
        FROM youtube_channels
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    return jsonify(
        [
            {
                "id": row["id"],
                "source_url": row["source_url"],
                "channel_id": row["channel_id"],
                "title": row["title"] or row["channel_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    )


@app.post("/api/youtube/channels")
def add_youtube_channel():
    payload = request.get_json(silent=True) or {}
    source_url = (payload.get("url") or "").strip()
    if not source_url:
        return jsonify({"error": "YouTube channel URL is required."}), 400

    try:
        resolved = resolve_youtube_channel(source_url)
        preview = fetch_youtube_channel_videos(resolved["channel_id"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return jsonify({"error": f"Channel not found. The YouTube channel may not exist, may have been deleted, or may not have a public feed."}), 400
        return jsonify({"error": f"Unable to add YouTube channel: HTTP Error {exc.code}: {exc.reason}"}), 400
    except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
        return jsonify({"error": f"Unable to add YouTube channel: {exc}"}), 400

    db = get_db()
    title = resolved["title"] or preview["title"]
    try:
        cursor = db.execute(
            """
            INSERT INTO youtube_channels (source_url, channel_id, title)
            VALUES (?, ?, ?)
            """,
            (resolved["source_url"], resolved["channel_id"], title),
        )
        db.commit()
    except sqlite3.IntegrityError:
        existing = db.execute(
            """
            SELECT id, source_url, channel_id, title, created_at
            FROM youtube_channels
            WHERE channel_id = ?
            """,
            (resolved["channel_id"],),
        ).fetchone()
        return (
            jsonify(
                {
                    "id": existing["id"],
                    "source_url": existing["source_url"],
                    "channel_id": existing["channel_id"],
                    "title": existing["title"] or existing["channel_id"],
                    "created_at": existing["created_at"],
                }
            ),
            200,
        )

    row = db.execute(
        """
        SELECT id, source_url, channel_id, title, created_at
        FROM youtube_channels
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return (
        jsonify(
            {
                "id": row["id"],
                "source_url": row["source_url"],
                "channel_id": row["channel_id"],
                "title": row["title"] or row["channel_id"],
                "created_at": row["created_at"],
            }
        ),
        201,
    )


@app.delete("/api/youtube/channels/<int:channel_row_id>")
def delete_youtube_channel(channel_row_id):
    db = get_db()
    cursor = db.execute("DELETE FROM youtube_channels WHERE id = ?", (channel_row_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Channel not found."}), 404
    return ("", 204)


@app.get("/api/youtube/videos")
def list_youtube_videos():
    # Check for refresh parameter
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    
    db = get_db()
    rows = db.execute(
        """
        SELECT id, source_url, channel_id, title
        FROM youtube_channels
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()

    videos = []
    errors = []
    source_info = {}

    for row in rows:
        # Check if cache file exists and is older than 24 hours
        cache_file = YOUTUBE_CACHE_DIR / f"{row['channel_id']}.json"
        should_force_refresh = force_refresh
        if not force_refresh and cache_file.exists():
            try:
                cache_age = time.time() - cache_file.stat().st_mtime
                # Automatically refresh if cache is older than 24 hours
                if cache_age > 3600 * 24:
                    should_force_refresh = True
            except OSError:
                pass
        
        try:
            # Pass force_refresh to fetch_youtube_channel_videos
            preview = fetch_youtube_channel_videos(row["channel_id"], force_refresh=should_force_refresh)
        except (urllib.error.HTTPError, urllib.error.URLError, ET.ParseError, ValueError) as exc:
            errors.append(
                {
                    "channel_id": row["channel_id"],
                    "channel_title": row["title"] or row["channel_id"],
                    "message": str(exc),
                }
            )
            continue

        if preview["title"] and preview["title"] != (row["title"] or ""):
            db.execute(
                "UPDATE youtube_channels SET title = ? WHERE id = ?",
                (preview["title"], row["id"]),
            )
            db.commit()

        # Store source info for this channel
        source_info[row["channel_id"]] = preview.get("_source", "unknown")

        for video in preview["videos"]:
            videos.append(
                {
                    "channel_row_id": row["id"],
                    "channel_id": row["channel_id"],
                    "channel_title": preview["title"],
                    "source_url": row["source_url"],
                    "video_id": video["video_id"],
                    "title": video["title"],
                    "link": video["link"],
                    "thumbnail": video["thumbnail"],
                    "published_at": video["published_at"],
                }
            )

    videos.sort(key=lambda video: video["published_at"], reverse=True)
    return jsonify({"videos": videos, "errors": errors, "sources": source_info})


@app.get("/api/podcasts/feeds")
def list_podcast_feeds():
    rows = get_db().execute(
        """
        SELECT id, url, title, category, created_at
        FROM podcast_feeds
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    return jsonify(
        [
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "category": row["category"] or "General",
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    )


@app.post("/api/podcasts/feeds")
def add_podcast_feed():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Podcast feed URL is required."}), 400

    try:
        preview = fetch_podcast_preview(url)
    except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
        return jsonify({"error": f"Unable to add podcast feed: {exc}"}), 400

    db = get_db()
    try:
        cursor = db.execute(
            "INSERT INTO podcast_feeds (url, title, category) VALUES (?, ?, ?)",
            (url, preview["title"], preview["category"]),
        )
        db.commit()
    except sqlite3.IntegrityError:
        existing = db.execute(
            "SELECT id, url, title, category, created_at FROM podcast_feeds WHERE url = ?",
            (url,),
        ).fetchone()
        return (
            jsonify(
                {
                    "id": existing["id"],
                    "url": existing["url"],
                    "title": existing["title"] or existing["url"],
                    "category": existing["category"] or "General",
                    "created_at": existing["created_at"],
                }
            ),
            200,
        )

    row = db.execute(
        "SELECT id, url, title, category, created_at FROM podcast_feeds WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return (
        jsonify(
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"] or row["url"],
                "category": row["category"] or "General",
                "created_at": row["created_at"],
            }
        ),
        201,
    )


@app.delete("/api/podcasts/feeds/<int:feed_id>")
def delete_podcast_feed(feed_id):
    db = get_db()
    cursor = db.execute("DELETE FROM podcast_feeds WHERE id = ?", (feed_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Podcast feed not found."}), 404
    return ("", 204)


@app.get("/api/podcasts/episodes")
def list_podcast_episodes():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, url, title, category
        FROM podcast_feeds
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()

    episodes = []
    errors = []

    for row in rows:
        try:
            preview = fetch_podcast_preview(row["url"])
        except (urllib.error.URLError, ET.ParseError, ValueError) as exc:
            errors.append(
                {
                    "feed_id": row["id"],
                    "feed_title": row["title"] or row["url"],
                    "message": str(exc),
                }
            )
            continue

        if preview["title"] != (row["title"] or "") or preview["category"] != (row["category"] or "General"):
            db.execute(
                "UPDATE podcast_feeds SET title = ?, category = ? WHERE id = ?",
                (preview["title"], preview["category"], row["id"]),
            )
            db.commit()

        for episode in preview["episodes"]:
            episodes.append(
                {
                    "feed_id": row["id"],
                    "feed_title": preview["title"],
                    "feed_category": preview["category"],
                    "title": episode["title"],
                    "description": episode["description"],
                    "audio_url": episode["audio_url"],
                    "link": episode["link"],
                    "published_at": episode["published_at"],
                    "category": episode["category"],
                }
            )

    episodes.sort(key=lambda episode: episode["published_at"], reverse=True)
    return jsonify({"episodes": episodes, "errors": errors})


@app.post("/api/terminal/execute")
def execute_terminal_command():
    payload = request.get_json(silent=True) or {}
    command = payload.get("command", "")
    cwd = payload.get("cwd", str(BASE_DIR))
    try:
        result = run_terminal_command(command, cwd)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Command timed out after 60 seconds."}), 408
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"error": f"Unable to execute command: {exc}"}), 500
    return jsonify(result)


@app.post("/api/ai/summary")
def generate_video_summary():
    payload = request.get_json(silent=True) or {}
    video_title = (payload.get("title") or "").strip()
    video_channel = (payload.get("channel") or "").strip()
    video_id = (payload.get("video_id") or "").strip()
    
    if not video_title:
        return jsonify({"error": "Video title is required."}), 400
    
    prompt = f"""You are a YouTube video analyzer. Provide a brief summary of what this video is about based on the information below.

Video Title: {video_title}
Channel: {video_channel}
Video ID: {video_id}

Provide a 2-3 sentence summary describing what the video is about, the main topic, and who would benefit from watching it. Be concise and informative."""

    try:
        import urllib.request
        import json
        
        ollama_request = {
            "model": "llama3:latest",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 300
            }
        }
        
        request_obj = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps(ollama_request).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        
        with urllib.request.urlopen(request_obj, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            
        summary = result.get("response", "").strip()
        
        if not summary:
            return jsonify({"error": "No summary generated."}), 500
            
        return jsonify({"summary": summary})
        
    except Exception as exc:
        return jsonify({"error": f"Failed to generate summary: {str(exc)}"}), 500


@app.get("/api/youtube/favorites")
def list_favorite_videos():
    db = get_db()
    rows = db.execute(
        """
        SELECT id, video_id, channel_id, channel_title, title, link, thumbnail, published_at, created_at
        FROM favorite_videos
        ORDER BY created_at DESC
        """
    ).fetchall()
    return jsonify(
        [
            {
                "id": row["id"],
                "video_id": row["video_id"],
                "channel_id": row["channel_id"],
                "channel_title": row["channel_title"],
                "title": row["title"],
                "link": row["link"],
                "thumbnail": row["thumbnail"],
                "published_at": row["published_at"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
    )


@app.post("/api/youtube/favorites")
def add_favorite_video():
    payload = request.get_json(silent=True) or {}
    video_id = (payload.get("video_id") or "").strip()
    channel_id = (payload.get("channel_id") or "").strip()
    channel_title = (payload.get("channel_title") or "").strip()
    title = (payload.get("title") or "").strip()
    link = (payload.get("link") or "").strip()
    thumbnail = (payload.get("thumbnail") or "").strip()
    published_at = (payload.get("published_at") or "").strip()
    
    if not video_id or not title:
        return jsonify({"error": "Video ID and title are required."}), 400
    
    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO favorite_videos (video_id, channel_id, channel_title, title, link, thumbnail, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (video_id, channel_id, channel_title, title, link, thumbnail, published_at),
        )
        db.commit()
    except sqlite3.IntegrityError:
        row = db.execute(
            "SELECT id, video_id, channel_id, channel_title, title, link, thumbnail, published_at, created_at FROM favorite_videos WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return jsonify(
            {
                "id": row["id"],
                "video_id": row["video_id"],
                "channel_id": row["channel_id"],
                "channel_title": row["channel_title"],
                "title": row["title"],
                "link": row["link"],
                "thumbnail": row["thumbnail"],
                "published_at": row["published_at"],
                "created_at": row["created_at"],
            }
        ), 200
    
    txt_path = BASE_DIR / "favorite_videos.txt"
    with open(txt_path, "a", encoding="utf-8") as f:
        f.write(f"{title}\n{video_id}\n{link}\n---\n")
    
    row = db.execute(
        "SELECT id, video_id, channel_id, channel_title, title, link, thumbnail, published_at, created_at FROM favorite_videos WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return jsonify(
        {
            "id": row["id"],
            "video_id": row["video_id"],
            "channel_id": row["channel_id"],
            "channel_title": row["channel_title"],
            "title": row["title"],
            "link": row["link"],
            "thumbnail": row["thumbnail"],
            "published_at": row["published_at"],
            "created_at": row["created_at"],
        }
    ), 201


@app.delete("/api/youtube/favorites/<int:favorite_id>")
def delete_favorite_video(favorite_id):
    db = get_db()
    cursor = db.execute("DELETE FROM favorite_videos WHERE id = ?", (favorite_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Favorite video not found."}), 404
    return ("", 204)


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5105, debug=True)
