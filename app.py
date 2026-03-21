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
import yaml
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
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS quick_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            title TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS list_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            pill_color TEXT DEFAULT 'violet',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            pill_color TEXT DEFAULT 'violet',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (list_id) REFERENCES list_definitions(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS list_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            field_type TEXT DEFAULT 'text',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (list_id) REFERENCES list_definitions(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            folders TEXT DEFAULT 'General',
            description TEXT,
            tags TEXT,
            is_favorite INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_lists = cursor.execute("SELECT type FROM list_definitions WHERE type IN ('movies', 'series', 'books', 'links', 'software')").fetchall()
    existing_types = set(row[0] for row in existing_lists)
    print(f"[DB] Existing list types: {existing_types}")
    
    default_lists = [
        ('Movies', 'movies', 'violet'),
        ('Series', 'series', 'blue'),
        ('Books', 'books', 'green'),
        ('Links', 'links', 'amber'),
        ('Software', 'software', 'rose'),
    ]
    for name, list_type, color in default_lists:
        if list_type not in existing_types:
            print(f"[DB] Adding list: {name}")
            cursor.execute(
                "INSERT INTO list_definitions (name, type, pill_color) VALUES (?, ?, ?)",
                (name, list_type, color)
            )
        else:
            print(f"[DB] Skipping list: {name} (already exists)")
    db.commit()
    db.close()
    print("[DB] Database initialized successfully")


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
            sort_order_val = payload.get("sort_order")
            if sort_order_val is not None and sort_order_val != "":
                sort_order = int(sort_order_val)
                updates.append("sort_order = ?")
                values.append(sort_order)
            else:
                # If sort_order is empty string or None, set it to NULL
                updates.append("sort_order = ?")
                values.append(None)
        except (TypeError, ValueError):
            return jsonify({"error": "sort_order must be an integer."}), 400

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


@app.get("/api/terminal/cwd")
def get_terminal_cwd():
    return jsonify({"cwd": str(BASE_DIR)})


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


@app.get("/api/ai/models")
def get_available_models():
    models = {"ollama": [], "openai": []}
    
    db = get_db()
    ollama_endpoint = db.execute("SELECT value FROM settings WHERE key = 'ollama_endpoint'").fetchone()
    openai_key = db.execute("SELECT value FROM settings WHERE key = 'openai_api_key'").fetchone()
    
    ollama_url = (ollama_endpoint["value"] if ollama_endpoint else "http://localhost:11434").strip().rstrip("/")
    api_key = openai_key["value"] if openai_key else ""
    
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            models["ollama"] = [m["name"] for m in result.get("models", [])]
    except Exception:
        models["ollama"] = ["llama3:latest", "llama3.1:latest", "llama3.2:latest", "mistral:latest", "codellama:latest", "phi3:latest"]
    
    if api_key:
        try:
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            with urllib.request.urlopen(req, timeout=15) as response:
                result = json.loads(response.read().decode("utf-8"))
                gpt_models = [m["id"] for m in result.get("data", []) if m["id"].startswith("gpt-")]
                models["openai"] = sorted(gpt_models, reverse=True)[:20]
        except Exception:
            models["openai"] = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]
    else:
        models["openai"] = ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]
    
    return jsonify(models)


@app.post("/api/ai/summary")
def generate_video_summary():
    payload = request.get_json(silent=True) or {}
    video_title = (payload.get("title") or "").strip()
    video_channel = (payload.get("channel") or "").strip()
    video_id = (payload.get("video_id") or "").strip()
    selected_model = (payload.get("model") or "").strip()
    
    print(f"[AI] Summary request - title: {video_title[:50] if video_title else 'None'}, model: {selected_model or 'default'}")
    
    db = get_db()
    ollama_setting = db.execute("SELECT value FROM settings WHERE key = 'ollama_endpoint'").fetchone()
    openai_setting = db.execute("SELECT value FROM settings WHERE key = 'openai_api_key'").fetchone()
    default_model_setting = db.execute("SELECT value FROM settings WHERE key = 'default_summary_model'").fetchone()
    
    if not selected_model:
        selected_model = default_model_setting["value"] if default_model_setting else "llama3:latest"
        print(f"[AI] Using default model: {selected_model}")
    
    ollama_endpoint = (ollama_setting["value"] if ollama_setting else "http://localhost:11434").strip().rstrip("/")
    api_key = openai_setting["value"] if openai_setting else ""
    
    model_lower = selected_model.lower()
    
    if ":" in selected_model or "/" in selected_model:
        is_openai = False
    else:
        known_openai_patterns = [
            "gpt-", "text-", "davinci", "ada", "babbage", "curie", "o1-", "o3-", "o4-"
        ]
        is_openai = any(model_lower.startswith(p) for p in known_openai_patterns)
    
    print(f"[AI] Using Ollama endpoint: {ollama_endpoint}, is OpenAI: {is_openai}, model: {selected_model}")
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
        
        if is_openai:
            if not api_key:
                return jsonify({"error": "OpenAI API key required for GPT models."}), 400
            
            uses_completion_tokens = any(x in selected_model.lower() for x in ["o1-", "o3-", "o4-", "gpt-4.5", "gpt-5"])
            
            if uses_completion_tokens:
                openai_request = {
                    "model": selected_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 300
                }
            else:
                openai_request = {
                    "model": selected_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.3
                }
            
            endpoint = "https://api.openai.com/v1/chat/completions"
            result_key = ("choices", 0, "message", "content")
            
            request_obj = urllib.request.Request(
                endpoint,
                data=json.dumps(openai_request).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            )
            
            try:
                with urllib.request.urlopen(request_obj, timeout=120) as response:
                    result = json.loads(response.read().decode("utf-8"))
                summary = result
                for key in result_key:
                    summary = summary[key]
                summary = summary.strip()
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8")
                print(f"[AI] OpenAI HTTP Error {e.code}: {error_body}")
                try:
                    error_data = json.loads(error_body)
                    error_msg = error_data.get("error", {}).get("message", error_body)
                except:
                    error_msg = error_body
                return jsonify({"error": f"OpenAI API Error: {error_msg}"}), e.code
        else:
            ollama_request = {
                "model": selected_model if selected_model else "llama3:latest",
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 300
                }
            }
            
            url = f"{ollama_endpoint}/api/generate"
            print(f"[AI] Sending request to: {url}")
            print(f"[AI] Request body: {json.dumps(ollama_request)}")
            
            request_obj = urllib.request.Request(
                url,
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
        print(f"[AI] Summary error: {exc}")
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


@app.get("/api/quick-links")
def list_quick_links():
    db = get_db()
    rows = db.execute(
        "SELECT id, url, title, created_at FROM quick_links ORDER BY created_at DESC"
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


@app.post("/api/quick-links")
def add_quick_link():
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or "").strip()
    
    if not url:
        return jsonify({"error": "URL is required."}), 400
    
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = "http://" + url
    except Exception:
        return jsonify({"error": "Invalid URL."}), 400
    
    db = get_db()
    cursor = db.execute(
        "INSERT INTO quick_links (url, title) VALUES (?, ?)",
        (url, title or None),
    )
    db.commit()
    row = db.execute(
        "SELECT id, url, title, created_at FROM quick_links WHERE id = ?",
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


@app.delete("/api/quick-links/<int:link_id>")
def delete_quick_link(link_id):
    db = get_db()
    cursor = db.execute("DELETE FROM quick_links WHERE id = ?", (link_id,))
    db.commit()
    if cursor.rowcount == 0:
        return jsonify({"error": "Quick link not found."}), 404
    return ("", 204)


@app.get("/api/settings/<key>")
def get_setting(key):
    db = get_db()
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return jsonify({"key": key, "value": row["value"] if row else None})


@app.put("/api/settings/<key>")
def set_setting(key):
    payload = request.get_json(silent=True) or {}
    value = (payload.get("value") or "").strip()
    
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    db.commit()
    return jsonify({"key": key, "value": value})


@app.get("/api/lists")
def get_lists():
    db = get_db()
    lists = db.execute("SELECT * FROM list_definitions ORDER BY created_at").fetchall()
    return jsonify([dict(row) for row in lists])


@app.post("/api/lists")
def create_list():
    db = get_db()
    data = request.get_json()
    name = data.get("name")
    list_type = data.get("type", "custom")
    pill_color = data.get("pill_color", "violet")
    
    cursor = db.execute(
        "INSERT INTO list_definitions (name, type, pill_color) VALUES (?, ?, ?)",
        (name, list_type, pill_color)
    )
    list_id = cursor.lastrowid
    
    custom_fields = data.get("fields", [])
    for field in custom_fields:
        db.execute(
            "INSERT INTO list_fields (list_id, field_name, field_type) VALUES (?, ?, ?)",
            (list_id, field["name"], field.get("type", "text"))
        )
    
    db.commit()
    
    new_list = db.execute("SELECT * FROM list_definitions WHERE id = ?", (list_id,)).fetchone()
    fields = db.execute("SELECT * FROM list_fields WHERE list_id = ?", (list_id,)).fetchall()
    
    return jsonify({
        "list": dict(new_list),
        "fields": [dict(f) for f in fields]
    }), 201


@app.get("/api/lists/<int:list_id>")
def get_list(list_id):
    db = get_db()
    lst = db.execute("SELECT * FROM list_definitions WHERE id = ?", (list_id,)).fetchone()
    if not lst:
        return jsonify({"error": "List not found"}), 404
    
    fields = db.execute("SELECT * FROM list_fields WHERE list_id = ?", (list_id,)).fetchall()
    items = db.execute("SELECT * FROM list_items WHERE list_id = ? ORDER BY created_at DESC", (list_id,)).fetchall()
    
    return jsonify({
        "list": dict(lst),
        "fields": [dict(f) for f in fields],
        "items": [dict(item) for item in items]
    })


@app.put("/api/lists/<int:list_id>")
def update_list(list_id):
    db = get_db()
    data = request.get_json()
    
    if "name" in data:
        db.execute("UPDATE list_definitions SET name = ? WHERE id = ?", (data["name"], list_id))
    if "pill_color" in data:
        db.execute("UPDATE list_definitions SET pill_color = ? WHERE id = ?", (data["pill_color"], list_id))
    
    db.commit()
    
    lst = db.execute("SELECT * FROM list_definitions WHERE id = ?", (list_id,)).fetchone()
    return jsonify(dict(lst))


@app.delete("/api/lists/<int:list_id>")
def delete_list(list_id):
    db = get_db()
    db.execute("DELETE FROM list_items WHERE list_id = ?", (list_id,))
    db.execute("DELETE FROM list_fields WHERE list_id = ?", (list_id,))
    db.execute("DELETE FROM list_definitions WHERE id = ?", (list_id,))
    db.commit()
    return jsonify({"success": True})


@app.get("/api/lists/<int:list_id>/items")
def get_list_items(list_id):
    db = get_db()
    items = db.execute("SELECT * FROM list_items WHERE list_id = ? ORDER BY created_at DESC", (list_id,)).fetchall()
    return jsonify([dict(item) for item in items])


@app.post("/api/lists/<int:list_id>/items")
def create_list_item(list_id):
    db = get_db()
    data = request.get_json()
    item_data = data.get("data", {})
    pill_color = data.get("pill_color", "violet")
    
    import json as json_lib
    cursor = db.execute(
        "INSERT INTO list_items (list_id, data, pill_color) VALUES (?, ?, ?)",
        (list_id, json_lib.dumps(item_data), pill_color)
    )
    db.commit()
    
    item = db.execute("SELECT * FROM list_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(item)), 201


@app.put("/api/lists/<int:list_id>/items/<int:item_id>")
def update_list_item(list_id, item_id):
    db = get_db()
    data = request.get_json()
    
    if "data" in data:
        import json as json_lib
        db.execute("UPDATE list_items SET data = ? WHERE id = ? AND list_id = ?", 
                   (json_lib.dumps(data["data"]), item_id, list_id))
    if "pill_color" in data:
        db.execute("UPDATE list_items SET pill_color = ? WHERE id = ? AND list_id = ?", 
                   (data["pill_color"], item_id, list_id))
    
    db.commit()
    
    item = db.execute("SELECT * FROM list_items WHERE id = ?", (item_id,)).fetchone()
    return jsonify(dict(item))


@app.delete("/api/lists/<int:list_id>/items/<int:item_id>")
def delete_list_item(list_id, item_id):
    db = get_db()
    db.execute("DELETE FROM list_items WHERE id = ? AND list_id = ?", (item_id, list_id))
    db.commit()
    return jsonify({"success": True})


@app.get("/api/lists/<int:list_id>/fields")
def get_list_fields(list_id):
    db = get_db()
    fields = db.execute("SELECT * FROM list_fields WHERE list_id = ?", (list_id,)).fetchall()
    return jsonify([dict(f) for f in fields])


@app.post("/api/lists/<int:list_id>/fields")
def add_list_field(list_id):
    db = get_db()
    data = request.get_json()
    
    cursor = db.execute(
        "INSERT INTO list_fields (list_id, field_name, field_type) VALUES (?, ?, ?)",
        (list_id, data["field_name"], data.get("field_type", "text"))
    )
    db.commit()
    
    field = db.execute("SELECT * FROM list_fields WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(field)), 201


@app.delete("/api/lists/<int:list_id>/fields/<int:field_id>")
def delete_list_field(list_id, field_id):
    db = get_db()
    db.execute("DELETE FROM list_fields WHERE id = ? AND list_id = ?", (field_id, list_id))
    db.commit()
    return jsonify({"success": True})


@app.get("/api/config/export")
def export_config():
    import yaml
    
    db = get_db()
    
    appearance_theme = db.execute("SELECT value FROM settings WHERE key = 'theme'").fetchone()
    appearance_palette = db.execute("SELECT value FROM settings WHERE key = 'palette'").fetchone()
    ollama_endpoint = db.execute("SELECT value FROM settings WHERE key = 'ollama_endpoint'").fetchone()
    openai_api_key = db.execute("SELECT value FROM settings WHERE key = 'openai_api_key'").fetchone()
    dashy_home = db.execute("SELECT value FROM settings WHERE key = 'dashy_home'").fetchone()
    
    quick_links = db.execute("SELECT url, title FROM quick_links ORDER BY created_at").fetchall()
    
    rss_feeds = db.execute("SELECT url, title FROM rss_feeds ORDER BY created_at").fetchall()
    podcasts = db.execute("SELECT url, title FROM podcast_feeds ORDER BY created_at").fetchall()
    
    youtube_channels = db.execute("SELECT channel_id, title FROM youtube_channels ORDER BY created_at").fetchall()
    
    config = {
        "appearance": {
            "theme": appearance_theme["value"] if appearance_theme else "light",
            "palette": appearance_palette["value"] if appearance_palette else "aurora",
        },
        "ai": {
            "ollama_endpoint": ollama_endpoint["value"] if ollama_endpoint else "http://localhost:11434",
            "openai_api_key": openai_api_key["value"] if openai_api_key else "",
        },
        "self_hosted": {
            "home_url": dashy_home["value"] if dashy_home else "",
            "quick_links": [{"url": row["url"], "title": row["title"]} for row in quick_links],
        },
        "rss": {
            "feeds": [{"url": row["url"], "title": row["title"] or row["url"]} for row in rss_feeds],
        },
        "podcasts": {
            "feeds": [{"url": row["url"], "title": row["title"] or row["url"]} for row in podcasts],
        },
        "youtube": {
            "channels": [{"channel_id": row["channel_id"], "title": row["title"]} for row in youtube_channels],
        },
    }
    
    config_path = Path("config.yml")
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    return jsonify({"message": "Config exported to config.yml", "path": str(config_path)})


@app.post("/api/config/import")
def import_config():
    import yaml
    
    payload = request.get_json(silent=True) or {}
    file_path = payload.get("path", "config.yml")
    
    config_path = Path(file_path)
    if not config_path.exists():
        return jsonify({"error": f"Config file not found: {file_path}"}), 404
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        return jsonify({"error": f"Failed to parse config: {str(e)}"}), 400
    
    db = get_db()
    imported = {"settings": [], "quick_links": 0, "rss_feeds": 0, "podcasts": 0, "youtube_channels": 0}
    
    if "appearance" in config:
        if "theme" in config["appearance"]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('theme', ?)", (config["appearance"]["theme"],))
            imported["settings"].append("theme")
        if "palette" in config["appearance"]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('palette', ?)", (config["appearance"]["palette"],))
            imported["settings"].append("palette")
    
    if "ai" in config:
        if "ollama_endpoint" in config["ai"]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ollama_endpoint', ?)", (config["ai"]["ollama_endpoint"],))
            imported["settings"].append("ollama_endpoint")
        if "openai_api_key" in config["ai"]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('openai_api_key', ?)", (config["ai"]["openai_api_key"],))
            imported["settings"].append("openai_api_key")
    
    if "self_hosted" in config:
        if "home_url" in config["self_hosted"]:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('dashy_home', ?)", (config["self_hosted"]["home_url"],))
            imported["settings"].append("dashy_home")
        if "quick_links" in config["self_hosted"]:
            for link in config["self_hosted"]["quick_links"]:
                try:
                    db.execute("INSERT INTO quick_links (url, title) VALUES (?, ?)", (link.get("url", ""), link.get("title", "")))
                    imported["quick_links"] += 1
                except Exception:
                    pass
    
    if "rss" in config and "feeds" in config["rss"]:
        for feed in config["rss"]["feeds"]:
            try:
                db.execute("INSERT INTO rss_feeds (url, title) VALUES (?, ?)", (feed.get("url", ""), feed.get("title", "")))
                imported["rss_feeds"] += 1
            except Exception:
                pass
    
    if "podcasts" in config and "feeds" in config["podcasts"]:
        for podcast in config["podcasts"]["feeds"]:
            try:
                db.execute("INSERT INTO podcast_feeds (url, title) VALUES (?, ?)", (podcast.get("url", ""), podcast.get("title", "")))
                imported["podcasts"] += 1
            except Exception:
                pass
    
    if "youtube" in config and "channels" in config["youtube"]:
        for channel in config["youtube"]["channels"]:
            try:
                db.execute("INSERT INTO youtube_channels (channel_id, title) VALUES (?, ?)", (channel.get("channel_id", ""), channel.get("title", "")))
                imported["youtube_channels"] += 1
            except Exception:
                pass
    
    db.commit()
    return jsonify({"message": "Config imported successfully", "imported": imported})


@app.get("/api/bookmarks")
def list_bookmarks():
    db = get_db()
    folder = request.args.get("folder", "")
    favorites_only = request.args.get("favorites", "false").lower() == "true"
    
    try:
        rows = db.execute("SELECT * FROM bookmarks").fetchall()
        print(f"[BOOKMARKS] Found {len(rows)} bookmarks in database")
    except Exception as e:
        print(f"[BOOKMARKS] Error querying bookmarks: {e}")
        return jsonify([])
    
    query = "SELECT * FROM bookmarks WHERE 1=1"
    params = []
    
    if folder and folder != "all":
        query += " AND folders LIKE ?"
        params.append(f"%{folder}%")
    
    if favorites_only:
        query += " AND is_favorite = 1"
    
    query += " ORDER BY is_favorite DESC, created_at DESC"
    
    rows = db.execute(query, params).fetchall()
    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict["folders"] = [f.strip() for f in row_dict.get("folders", "").split("|") if f.strip()]
        result.append(row_dict)
    print(f"[BOOKMARKS] Returning {len(result)} bookmarks")
    return jsonify(result)


@app.get("/api/bookmarks/folders")
def list_bookmark_folders():
    db = get_db()
    rows = db.execute("SELECT folders FROM bookmarks").fetchall()
    all_folders = set()
    for row in rows:
        folders_str = row["folders"] or ""
        for f in folders_str.split("|"):
            f = f.strip()
            if f:
                all_folders.add(f)
    return jsonify(sorted(all_folders))


@app.post("/api/bookmarks")
def create_bookmark():
    db = get_db()
    data = request.get_json() or {}
    
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    folder_input = (data.get("folder") or "General").strip()
    description = (data.get("description") or "").strip()
    
    folders_list = [f.strip() for f in folder_input.split(",") if f.strip()]
    if not folders_list:
        folders_list = ["General"]
    folders_str = "|".join(folders_list)
    
    print(f"[BOOKMARKS] Creating bookmark: name={name}, url={url}, folders={folders_str}")
    
    if not name or not url:
        return jsonify({"error": "Name and URL are required"}), 400
    
    cursor = db.execute(
        "INSERT INTO bookmarks (name, url, folders, description) VALUES (?, ?, ?, ?)",
        (name, url, folders_str, description)
    )
    db.commit()
    print(f"[BOOKMARKS] Inserted bookmark with id: {cursor.lastrowid}")
    
    bookmark = db.execute("SELECT * FROM bookmarks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    bookmark_dict = dict(bookmark)
    bookmark_dict["folders"] = folders_list
    return jsonify(bookmark_dict), 201


@app.put("/api/bookmarks/<int:bookmark_id>")
def update_bookmark(bookmark_id):
    db = get_db()
    data = request.get_json() or {}
    
    bookmark = db.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
    if not bookmark:
        return jsonify({"error": "Bookmark not found"}), 404
    
    name = (data.get("name") or bookmark["name"]).strip()
    url = (data.get("url") or bookmark["url"]).strip()
    folder_input = (data.get("folder") or bookmark.get("folders", "General") or "General").strip()
    description = (data.get("description") or bookmark["description"] or "").strip()
    is_favorite = data.get("is_favorite", bookmark["is_favorite"])
    
    folders_list = [f.strip() for f in folder_input.split(",") if f.strip()]
    if not folders_list:
        folders_list = ["General"]
    folders_str = "|".join(folders_list)
    
    db.execute(
        "UPDATE bookmarks SET name = ?, url = ?, folders = ?, description = ?, is_favorite = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (name, url, folders_str, description, is_favorite, bookmark_id)
    )
    db.commit()
    
    updated = db.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
    updated_dict = dict(updated)
    updated_dict["folders"] = folders_list
    return jsonify(updated_dict)


@app.put("/api/bookmarks/<int:bookmark_id>/favorite")
def toggle_bookmark_favorite(bookmark_id):
    db = get_db()
    
    bookmark = db.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
    if not bookmark:
        return jsonify({"error": "Bookmark not found"}), 404
    
    new_favorite = 0 if bookmark["is_favorite"] else 1
    db.execute("UPDATE bookmarks SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_favorite, bookmark_id))
    db.commit()
    
    updated = db.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
    return jsonify(dict(updated))


@app.delete("/api/bookmarks/<int:bookmark_id>")
def delete_bookmark(bookmark_id):
    db = get_db()
    
    bookmark = db.execute("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,)).fetchone()
    if not bookmark:
        return jsonify({"error": "Bookmark not found"}), 404
    
    db.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
    db.commit()
    
    return jsonify({"message": "Bookmark deleted"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5105, debug=True)
