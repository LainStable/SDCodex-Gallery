"""SDCodex Gallery backend — disk-backed browsing, metadata extraction, and workflow export."""

import io
import json
import mimetypes
import os
import re
import tempfile
import time
import zipfile
from urllib.parse import unquote
from flask import Blueprint, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageSequence

try:
    from sd_prompt_reader.image_data_reader import ImageDataReader
except Exception:
    ImageDataReader = None

try:
    import piexif
    import piexif.helper
except Exception:
    piexif = None

gallery_bp = Blueprint("gallery_bp", __name__, url_prefix="/gallery/api")


def _core_session_missing() -> bool:
    """True when SDCodex is running and the request has no valid session.

    Standalone plugin deployments have no core auth module, so they keep
    working unrestricted (as before).
    """
    try:
        from sdcodex import auth as core_auth
    except Exception:
        return False
    try:
        return core_auth.current_user() is None
    except Exception:
        return True


def require_login():
    """Blueprint guard shared by /gallery/api and the /gallery2 alias."""
    if _core_session_missing():
        return jsonify({"error": "unauthorized"}), 401
    return None


@gallery_bp.before_request
def _gallery_auth_guard():
    return require_login()


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif",
    ".avif", ".heic", ".heif", ".jfif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

_meta_cache = {}  # (path, mtime) -> dict
_thumb_cache_dir = None


@gallery_bp.get("/legacy-ui/<path:filename>")
def legacy_ui_asset(filename):
    """Serve the previous FolderFrame gallery layout and its static assets."""
    return send_from_directory(os.path.join(_plugin_dir(), "legacy-ui"), filename)


def _plugin_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _folderframe_listing(root, target):
    import html
    from urllib.parse import quote

    rel = os.path.relpath(target, root)
    rel = "" if rel == "." else rel.replace(os.sep, "/").strip("/") + "/"
    rows = []
    try:
        entries = sorted(os.scandir(target), key=lambda entry: (not entry.is_dir(), entry.name.casefold()))
    except OSError:
        entries = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        suffix = "/" if entry.is_dir(follow_symlinks=False) else ""
        href = quote(entry.name, safe="") + suffix
        rows.append(f'<li><a href="{href}">{html.escape(entry.name)}{suffix}</a></li>')
    title = html.escape("/" + rel)
    return (
        '<!doctype html><meta charset="utf-8"><title>Gallery folders</title>'
        f'<h1>{title}</h1><ul>{"".join(rows)}</ul>'
    )


def _folderframe_sources():
    """Local disk plus one FolderFrame source per enabled Immich server.

    Remote servers are advertised as ordinary web directories so the gallery's
    source switcher, breadcrumbs, grid and viewer need no remote-specific code.
    """
    sources = [{"id": "media", "label": "Local", "path": "/gallery/api/browse/", "discoveryMode": "directory"}]
    try:
        from immich_backend import enabled_servers

        for row in enabled_servers():
            base = f"/gallery/api/immich/{row['id']}"
            sources.append({
                "id": row["id"],
                "label": row["name"],
                "path": f"{base}/browse/",
                "thumbnailPath": f"{base}/thumb/",
                "discoveryMode": "directory",
            })
    except Exception:
        pass
    return sources


@gallery_bp.get("/folderframe.config.json")
def folderframe_config():
    return jsonify({
        "sources": _folderframe_sources(),
        "defaults": {"source": "media", "album": "", "view": "folders", "sort": "filename", "interval": 5, "imageMode": "fit", "shuffle": False, "autoRefresh": True, "tvMode": False, "autoplay": False, "controls": True, "showFilenames": True, "showDownloadButton": True, "showCopyButton": True},
        "index": {"refreshInterval": 120},
        "embed": {"refreshInterval": 300, "rememberPreferences": False},
    })


@gallery_bp.get("/optimize-status")
def optimize_status_compat():
    # The old gallery UI polls this endpoint while an optimize task is active.
    # The current optimizer reports progress in its POST response instead.
    return jsonify({"running": False, "status": "idle"})


@gallery_bp.get("/browse/")
@gallery_bp.get("/browse/<path:relpath>")
def folderframe_browse(relpath=""):
    root = resolve_browse_root()
    target = os.path.abspath(os.path.join(root, unquote(relpath)))
    if not is_safe_path(root, target):
        return "Forbidden", 403
    if os.path.isdir(target):
        return Response(_folderframe_listing(root, target), mimetype="text/html")
    if os.path.isfile(target):
        mime, _ = mimetypes.guess_type(target)
        response = send_file(target, mimetype=mime or "application/octet-stream")
        response.headers["Content-Disposition"] = "inline"
        return response
    return "Not found", 404


def _workspace_root():
    cur = _plugin_dir()
    for _ in range(5):
        if os.path.isdir(os.path.join(cur, "plugins")) and os.path.isdir(os.path.join(cur, "backend")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(os.path.join(_plugin_dir(), "..", ".."))


def resolve_browse_root():
    """Resolve the configured gallery root, preferring explicit volume settings."""
    ws = _workspace_root()
    env_root = os.environ.get("GALLERY_ROOT") or os.environ.get("GALLERY")
    if env_root:
        # Keep the configured target even when its mount is absent so status
        # reports the real problem instead of silently browsing another folder.
        return os.path.abspath(os.path.expanduser(env_root))

    candidates = [
        os.path.join(ws, "downloads"),
        "/data/downloads",
        os.path.join(ws, "models"),
        os.path.join(_plugin_dir(), "downloads"),
    ]
    for cand in candidates:
        if cand and os.path.isdir(cand) and os.access(cand, os.W_OK):
            return os.path.abspath(cand)
    for cand in candidates:
        if cand and os.path.isdir(cand):
            return os.path.abspath(cand)
    fallback = os.path.join(ws, "downloads")
    os.makedirs(fallback, exist_ok=True)
    return os.path.abspath(fallback)


def is_safe_path(base, target):
    resolved_base = os.path.realpath(base)
    resolved_target = os.path.realpath(target)
    try:
        return os.path.commonpath((resolved_base, resolved_target)) == resolved_base
    except ValueError:
        return False


def resolve_file(relpath):
    root = resolve_browse_root()
    clean = unquote(relpath or "").strip("/\\")
    target = os.path.join(root, clean)
    if not is_safe_path(root, target) or not os.path.isfile(target):
        return None
    return target


def extract_comfy_workflow(image_path):
    """Extract ComfyUI workflow JSON from PNG/WebP/JPEG metadata."""
    def extract_json_if_valid(val):
        if not isinstance(val, str):
            val = str(val)
        val = val.strip()
        if val.startswith("{") or val.startswith("["):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, (dict, list)):
                    return val
            except Exception:
                pass
        return None

    try:
        with Image.open(image_path) as img:
            img.load()
            if "workflow" in img.info:
                val = extract_json_if_valid(img.info["workflow"])
                if val:
                    return val
            if "prompt" in img.info:
                val = extract_json_if_valid(img.info["prompt"])
                if val:
                    return val
            if "exif" in img.info and piexif:
                try:
                    exif_dict = piexif.load(img.info["exif"])
                    if "Exif" in exif_dict and piexif.ExifIFD.UserComment in exif_dict["Exif"]:
                        user_comment = exif_dict["Exif"][piexif.ExifIFD.UserComment]
                        try:
                            comment_str = piexif.helper.UserComment.load(user_comment)
                        except Exception:
                            if isinstance(user_comment, bytes):
                                comment_str = user_comment.decode("utf-8", errors="ignore")
                                if comment_str.startswith("ASCII\0\0\0") or comment_str.startswith("UNICODE\0"):
                                    comment_str = comment_str[8:]
                            else:
                                comment_str = str(user_comment)
                        val = extract_json_if_valid(comment_str)
                        if val:
                            return val
                except Exception:
                    pass
    except Exception:
        pass
    return None


def read_sidecar_caption(image_path):
    base, _ = os.path.splitext(image_path)
    txt_path = base + ".txt"
    if os.path.isfile(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as fh:
                return fh.read().strip()
        except Exception:
            pass
    return ""


def read_embedded_caption(image_path):
    """Read a fallback caption embedded in common PNG text or EXIF fields."""
    try:
        with Image.open(image_path) as img:
            for key in ("Description", "parameters", "Comment", "title"):
                value = img.info.get(key)
                if value:
                    value = str(value).strip()
                    if value and not value.startswith(("{", "[")):
                        return value
            if piexif and img.info.get("exif"):
                try:
                    exif = piexif.load(img.info["exif"])
                    value = exif.get("0th", {}).get(piexif.ImageIFD.ImageDescription, "")
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="ignore")
                    if str(value).strip():
                        return str(value).strip()
                except Exception:
                    pass
    except Exception:
        pass
    return ""


def _parse_params_string(param_str):
    """Parse key-value parameters from Automatic1111 / Forge 'parameters' chunk."""
    res = {
        "model": "",
        "sampler": "",
        "steps": "",
        "cfg": "",
        "seed": "",
    }
    if not param_str:
        return res
    # Examples: Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 12345, Size: 512x512, Model: sdxl_base
    m_steps = re.search(r"\bSteps:\s*([^,\n]+)", param_str, re.IGNORECASE)
    if m_steps:
        res["steps"] = m_steps.group(1).strip()
    m_sampler = re.search(r"\bSampler:\s*([^,\n]+)", param_str, re.IGNORECASE)
    if m_sampler:
        res["sampler"] = m_sampler.group(1).strip()
    m_cfg = re.search(r"\bCFG scale:\s*([^,\n]+)", param_str, re.IGNORECASE)
    if m_cfg:
        res["cfg"] = m_cfg.group(1).strip()
    m_seed = re.search(r"\bSeed:\s*([^,\n]+)", param_str, re.IGNORECASE)
    if m_seed:
        res["seed"] = m_seed.group(1).strip()
    m_model = re.search(r"\bModel:\s*([^,\n]+)", param_str, re.IGNORECASE)
    if m_model:
        res["model"] = m_model.group(1).strip()
    return res


def read_prompts_and_settings(image_path):
    """Read SD positive/negative prompts and generation settings."""
    pos = ""
    neg = ""
    setting = ""
    parsed_params = {"model": "", "sampler": "", "steps": "", "cfg": "", "seed": ""}

    # 1. Try ImageDataReader if installed
    if ImageDataReader:
        try:
            reader = ImageDataReader(image_path)
            pos = str(reader.positive).strip() if getattr(reader, "positive", None) else ""
            neg = str(reader.negative).strip() if getattr(reader, "negative", None) else ""
            setting = str(reader.setting).strip() if getattr(reader, "setting", None) else ""
            if setting:
                parsed_params = _parse_params_string(setting)
        except Exception:
            pass

    # 2. Direct PIL inspection fallback
    if not pos and not setting:
        try:
            with Image.open(image_path) as img:
                raw_params = img.info.get("parameters") or img.info.get("Description") or ""
                if raw_params and isinstance(raw_params, str):
                    # Format: Positive prompt\nNegative prompt: ...\nSteps: ...
                    parts = raw_params.split("Negative prompt:")
                    if len(parts) > 1:
                        pos = parts[0].strip()
                        neg_and_rest = parts[1].split("Steps:")
                        neg = neg_and_rest[0].strip()
                        if len(neg_and_rest) > 1:
                            setting = "Steps: " + neg_and_rest[1].strip()
                    else:
                        steps_parts = raw_params.split("Steps:")
                        if len(steps_parts) > 1:
                            pos = steps_parts[0].strip()
                            setting = "Steps: " + steps_parts[1].strip()
                        else:
                            pos = raw_params.strip()
                    if setting:
                        parsed_params = _parse_params_string(setting)

                # ComfyUI prompt JSON inspection fallback
                if not pos and "prompt" in img.info:
                    try:
                        pjson = json.loads(img.info["prompt"])
                        if isinstance(pjson, dict):
                            for node in pjson.values():
                                if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                                    txt = node.get("inputs", {}).get("text", "")
                                    if txt and not pos:
                                        pos = str(txt).strip()
                                    elif txt and pos and not neg:
                                        neg = str(txt).strip()
                                if isinstance(node, dict) and "KSampler" in str(node.get("class_type", "")):
                                    inputs = node.get("inputs", {})
                                    if inputs:
                                        parsed_params["steps"] = str(inputs.get("steps", ""))
                                        parsed_params["cfg"] = str(inputs.get("cfg", ""))
                                        parsed_params["seed"] = str(inputs.get("seed", ""))
                                        parsed_params["sampler"] = str(inputs.get("sampler_name", ""))
                    except Exception:
                        pass
        except Exception:
            pass

    return {
        "sdPrompt": pos,
        "sdNegative": neg,
        "sdSetting": setting,
        "params": parsed_params,
    }


def guess_model_badge(filename, prompt_text, width, height):
    f_lower = (filename or "").lower()
    p_lower = (prompt_text or "").lower()
    combo = f"{f_lower} {p_lower}"
    if "flux" in combo:
        return "FLUX"
    if "sdxl" in combo or (width and height and max(width, height) >= 1024):
        return "SDXL"
    if "sd3" in combo:
        return "SD3"
    if "lora" in combo:
        return "LoRA"
    if "illustrious" in combo or "pony" in combo:
        return "PONY"
    return "SD1.5"


def format_bytes(n):
    if not n:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


# --------------------------------------------------------------------------- #
#  Endpoints
# --------------------------------------------------------------------------- #

@gallery_bp.get("/status")
def gallery_status():
    root = resolve_browse_root()
    exists = os.path.isdir(root)
    total_files = 0
    total_dirs = 0
    if exists:
        try:
            for _, dirs, files in os.walk(root):
                total_dirs += len(dirs)
                for f in files:
                    ext = os.path.splitext(f)[1].lower()
                    if ext in MEDIA_EXTENSIONS:
                        total_files += 1
        except Exception:
            pass
    return jsonify({
        "root": root,
        "exists": exists,
        "total_files": total_files,
        "total_folders": total_dirs,
        "writable": os.access(root, os.W_OK) if exists else False,
    })


@gallery_bp.get("/folders")
def list_folders():
    root = resolve_browse_root()
    if not os.path.isdir(root):
        return jsonify({"root": root, "folders": []})
    current = unquote(request.args.get("path", "")).strip("/\\")
    target = os.path.join(root, current) if current else root
    if not is_safe_path(root, target) or not os.path.isdir(target):
        return jsonify({"error": "Folder not found"}), 404
    folders = []
    if current:
        parent = os.path.dirname(current.rstrip("/\\"))
        folders.append({"path": parent, "name": "..", "count": 0, "is_parent": True})
    else:
        try:
            direct_count = sum(
                1 for name in os.listdir(target)
                if os.path.isfile(os.path.join(target, name))
                and os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS
            )
        except OSError:
            direct_count = 0
        folders.append({"path": "", "name": "All Media", "count": direct_count, "is_root": True})
    try:
        for entry in sorted(os.listdir(target)):
            p = os.path.join(target, entry)
            if not os.path.isdir(p) or entry.startswith(".") or entry in ("__pycache__", "node_modules"):
                continue
            count = 0
            for sub_root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
                for f in files:
                    if os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS:
                        count += 1
            folders.append({
                "path": os.path.join(current, entry) if current else entry,
                "name": entry,
                "count": count,
                "is_root": False,
            })
    except Exception:
        pass

    return jsonify({"root": root, "folders": folders})


@gallery_bp.get("/media")
def list_media():
    root = resolve_browse_root()
    if not os.path.isdir(root):
        return jsonify({"items": [], "total": 0})

    req_folder = unquote(request.args.get("folder", "")).strip("/\\")
    sort_mode = request.args.get("sort", "date_desc")
    filter_workflow = request.args.get("filter_workflow", "0") in ("1", "true")
    filter_sidecar = request.args.get("filter_sidecar", "0") in ("1", "true")
    filter_4k = request.args.get("filter_4k", "0") in ("1", "true")

    target_dir = os.path.join(root, req_folder) if req_folder else root
    if not is_safe_path(root, target_dir) or not os.path.isdir(target_dir):
        return jsonify({"error": "Folder not found"}), 404

    items = []
    # If folder is specified, only scan that folder. If root (""), list immediate files plus subfolder samples
    entries = []
    try:
        if req_folder:
            for item in os.listdir(target_dir):
                full = os.path.join(target_dir, item)
                if os.path.isfile(full) and os.path.splitext(item)[1].lower() in MEDIA_EXTENSIONS:
                    entries.append((full, req_folder, item))
        else:
            # Walk top level and one subfolder level for "All Media"
            for r, dirs, files in os.walk(target_dir):
                # Don't recurse into hidden or system dirs
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
                rel_dir = os.path.relpath(r, root)
                if rel_dir == ".":
                    rel_dir = ""
                for f in files:
                    if os.path.splitext(f)[1].lower() in MEDIA_EXTENSIONS:
                        full = os.path.join(r, f)
                        entries.append((full, rel_dir, f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    for full_path, folder, fname in entries:
        try:
            stat = os.stat(full_path)
            mtime = stat.st_mtime
            size = stat.st_size
            ext = os.path.splitext(fname)[1].lower()
            rel_file = os.path.join(folder, fname) if folder else fname

            # Read dimensions & workflow info
            cache_key = (full_path, mtime)
            cached = _meta_cache.get(cache_key)
            if not cached:
                width, height = 0, 0
                has_wf = False
                if ext in IMAGE_EXTENSIONS:
                    try:
                        with Image.open(full_path) as img:
                            width, height = img.size
                            has_wf = "workflow" in img.info or "prompt" in img.info
                    except Exception:
                        pass
                has_txt = os.path.isfile(os.path.splitext(full_path)[0] + ".txt")
                badge = guess_model_badge(fname, "", width, height)
                cached = {
                    "width": width,
                    "height": height,
                    "hasWorkflow": has_wf,
                    "hasSidecar": has_txt,
                    "modelBadge": badge,
                }
                _meta_cache[cache_key] = cached

            # Apply filters
            if filter_workflow and not cached["hasWorkflow"]:
                continue
            if filter_sidecar and not cached["hasSidecar"]:
                continue
            if filter_4k and (cached["width"] < 3840 and cached["height"] < 2160):
                continue

            items.append({
                "name": fname,
                "relpath": rel_file,
                "folder": folder,
                "size": size,
                "size_fmt": format_bytes(size),
                "mtime": mtime,
                "format": ext.lstrip(".").upper(),
                "kind": "video" if ext in VIDEO_EXTENSIONS else "image",
                "width": cached["width"],
                "height": cached["height"],
                "hasWorkflow": cached["hasWorkflow"],
                "hasSidecar": cached["hasSidecar"],
                "modelBadge": cached["modelBadge"],
                "url": f"/gallery/api/file/{rel_file}",
            })
        except Exception:
            continue

    # Sorting
    if sort_mode == "date_desc":
        items.sort(key=lambda x: x["mtime"], reverse=True)
    elif sort_mode == "date_asc":
        items.sort(key=lambda x: x["mtime"])
    elif sort_mode == "size_desc":
        items.sort(key=lambda x: x["size"], reverse=True)
    elif sort_mode == "name_asc":
        items.sort(key=lambda x: x["name"].lower())

    return jsonify({"items": items, "total": len(items), "folder": req_folder})


@gallery_bp.get("/file/<path:relpath>")
def get_media_file(relpath):
    target = resolve_file(relpath)
    if not target:
        return "File not found", 404
    mime, _ = mimetypes.guess_type(target)
    resp = send_file(target, mimetype=mime or "application/octet-stream")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@gallery_bp.get("/meta")
def get_media_meta():
    req_path = request.args.get("path") or os.path.join(request.args.get("folder", ""), request.args.get("file", ""))
    target = resolve_file(req_path)
    if not target:
        return jsonify({"ok": False, "error": "File not found"}), 404

    is_video = os.path.splitext(target)[1].lower() in VIDEO_EXTENSIONS
    caption = read_sidecar_caption(target) or (read_embedded_caption(target) if not is_video else "")
    sd_info = read_prompts_and_settings(target) if not is_video else {
        "sdPrompt": "", "sdNegative": "", "sdSetting": "", "params": {},
    }
    wf_json = extract_comfy_workflow(target) if not is_video else None

    stat = os.stat(target)
    width, height = 0, 0
    try:
        with Image.open(target) as img:
            width, height = img.size
    except Exception:
        pass

    fname = os.path.basename(target)
    ext = os.path.splitext(fname)[1].lstrip(".").upper()

    return jsonify({
        "ok": True,
        "fileName": fname,
        "relpath": req_path,
        "size": stat.st_size,
        "size_fmt": format_bytes(stat.st_size),
        "width": width,
        "height": height,
        "format": ext,
        "kind": "video" if is_video else "image",
        "caption": caption,
        "hasCaption": bool(caption),
        "hasSidecar": os.path.isfile(os.path.splitext(target)[0] + ".txt"),
        "sdPrompt": sd_info["sdPrompt"],
        "sdNegative": sd_info["sdNegative"],
        "sdSetting": sd_info["sdSetting"],
        "params": sd_info["params"],
        "hasWorkflow": wf_json is not None,
        "workflowJson": wf_json,
    })


@gallery_bp.post("/caption")
def save_caption():
    data = request.get_json(silent=True) or {}
    relpath = data.get("path") or os.path.join(data.get("folder", ""), data.get("file", ""))
    caption_text = data.get("caption", "")

    target = resolve_file(relpath)
    if not target:
        return jsonify({"ok": False, "error": "Invalid file target"}), 404

    txt_path = os.path.splitext(target)[0] + ".txt"
    try:
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(caption_text.strip())
        return jsonify({"ok": True, "caption": caption_text.strip(), "path": txt_path})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@gallery_bp.get("/workflow")
def download_workflow():
    req_path = request.args.get("path") or os.path.join(request.args.get("folder", ""), request.args.get("file", ""))
    target = resolve_file(req_path)
    if not target:
        return "File not found", 404

    wf = extract_comfy_workflow(target)
    if not wf:
        return "No ComfyUI workflow found", 404

    try:
        formatted = json.dumps(json.loads(wf), indent=2)
    except Exception:
        formatted = wf

    stem = os.path.splitext(os.path.basename(target))[0]
    return Response(
        formatted,
        mimetype="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}_workflow.json"'
        },
    )


@gallery_bp.post("/rescan")
def rescan_cache():
    global _meta_cache
    _meta_cache.clear()
    return jsonify({"ok": True, "message": "Cache invalidated"})


@gallery_bp.get("/caption-target")
def caption_target():
    """Resolve a gallery file into the absolute container folder+fileName that
    ComfyCaption's /api/caption-single endpoint expects.

    The gallery addresses media relative to its browse root; the captioning
    plugin wants an absolute folderPath + fileName so the viewer's
    "Caption this Image" button can point at the file currently on screen.
    """
    req_path = request.args.get("path") or os.path.join(
        request.args.get("folder", ""), request.args.get("file", "")
    )
    target = resolve_file(req_path)
    if not target:
        return jsonify({"ok": False, "error": "Invalid or missing file"}), 404
    return jsonify({
        "ok": True,
        "folderPath": os.path.dirname(target),
        "fileName": os.path.basename(target),
    })


@gallery_bp.get("/prompt")
def get_prompt():
    """Return the SD positive/negative prompt for a gallery file
    (used by ComfyUI custom nodes polling the gallery API)."""
    req_path = request.args.get("path") or os.path.join(
        request.args.get("folder", ""), request.args.get("file", "")
    )
    target = resolve_file(req_path)
    if not target:
        return jsonify({"error": "Invalid or missing file"}), 404
    sd = read_prompts_and_settings(target)
    return jsonify({"file": os.path.basename(target), **sd})


def _in_docker():
    return os.path.exists("/.dockerenv") or os.environ.get("REMBG_OUTPUT", "").startswith("/data")


def _docker_default_gateway():
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000" and parts[2] != "00000000":
                    import socket, struct
                    return socket.inet_ntoa(struct.pack("<I", int(parts[2], 16)))
    except Exception:
        pass
    return "172.17.0.1"


@gallery_bp.get("/browse-folders")
def browse_folders():
    """Web-based directory browser for selecting caption/rembg target folders."""
    req_path = request.args.get("path") or "/data"
    req_path = os.path.abspath(os.path.expanduser(req_path.strip("\"'")))
    if not os.path.exists(req_path):
        return jsonify({"error": f"Folder does not exist: {req_path}"}), 400
    if not os.path.isdir(req_path):
        req_path = os.path.dirname(req_path)
    try:
        entries = sorted(os.listdir(req_path))
    except PermissionError:
        return jsonify({"error": f"Permission denied: {req_path}"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    dirs = []
    for name in entries:
        if name.startswith("."):
            continue
        full = os.path.join(req_path, name)
        try:
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full})
        except Exception:
            continue
        if len(dirs) >= 500:
            break

    parent = os.path.dirname(req_path.rstrip(os.sep)) or os.sep
    return jsonify({
        "cwd": req_path,
        "parent": parent,
        "dirs": dirs,
        "isDocker": _in_docker(),
    })


@gallery_bp.get("/browse-roots")
def browse_roots():
    """Quick-pick roots for the folder browser."""
    candidates = [
        "/data", "/data/downloads", "/data/tasks", "/data/config",
        os.path.expanduser("~"), "/app", "/",
    ]
    seen = []
    for p in candidates:
        try:
            ap = os.path.abspath(p)
            if ap not in seen and os.path.isdir(ap):
                seen.append(ap)
        except Exception:
            continue
    return jsonify({"roots": seen, "isDocker": _in_docker()})


@gallery_bp.get("/export-workflows")
def export_workflows():
    root = resolve_browse_root()
    req_folder = unquote(request.args.get("folder", "")).strip("/\\")
    target_dir = os.path.join(root, req_folder) if req_folder else root
    if not is_safe_path(root, target_dir) or not os.path.isdir(target_dir):
        return "Folder not found", 404

    mem_zip = io.BytesIO()
    count = 0
    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r, _, files in os.walk(target_dir):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                    img_path = os.path.join(r, f)
                    wf = extract_comfy_workflow(img_path)
                    if wf:
                        stem = os.path.splitext(f)[0]
                        try:
                            formatted = json.dumps(json.loads(wf), indent=2)
                        except Exception:
                            formatted = wf
                        zf.writestr(f"{stem}_workflow.json", formatted)
                        count += 1

    if count == 0:
        return jsonify({"ok": False, "error": "No embedded workflows found in this folder"}), 404

    mem_zip.seek(0)
    folder_name = req_folder or "all_workflows"
    return send_file(
        mem_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{folder_name}_workflows.zip",
    )


def _operation_target(value):
    root = os.path.realpath(resolve_browse_root())
    target = os.path.realpath(os.path.join(root, str(value or "").strip("/\\")))
    if not is_safe_path(root, target):
        return None
    return target


def _optimize_image(path):
    ext = os.path.splitext(path)[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("Only JPEG, PNG and WebP images can be optimized")
    source_size = os.path.getsize(path)
    temp_path = None
    try:
        with Image.open(path) as source:
            image = source
            save_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[ext]
            options = {"optimize": True}
            if save_format == "JPEG":
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                # Preserve the source JPEG quantization tables; optimization
                # should reduce file size without silently lowering quality.
                options.update(quality="keep", subsampling="keep", exif=source.info.get("exif", b""))
            elif save_format == "WEBP":
                options.update(lossless=True, method=6)
            with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), suffix=ext, delete=False) as tmp:
                temp_path = tmp.name
            image.save(temp_path, format=save_format, **options)
        optimized_size = os.path.getsize(temp_path)
        if optimized_size < source_size:
            os.replace(temp_path, path)
            temp_path = None
            return source_size, optimized_size, True
        return source_size, source_size, False
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@gallery_bp.post("/optimize")
def optimize_media():
    data = request.get_json(silent=True) or {}
    path = _operation_target(data.get("path")) if data.get("path") else None
    folder = _operation_target(data.get("folder")) if "folder" in data else None
    if path is None and folder is None:
        return jsonify({"error": "path or folder is required"}), 400
    if folder is not None and not os.path.isdir(folder):
        return jsonify({"error": "Folder not found"}), 404
    targets = [path] if path else [
        _operation_target(os.path.relpath(os.path.join(folder, name), os.path.realpath(resolve_browse_root())))
        for name in sorted(os.listdir(folder))
        if os.path.isfile(os.path.join(folder, name))
        and os.path.splitext(name)[1].lower() in {".jpg", ".jpeg", ".png", ".webp"}
    ]
    targets = [target for target in targets if target]
    if path and not os.path.isfile(path):
        return jsonify({"error": "Image not found"}), 404
    before = after = optimized = 0
    errors = []
    for target in targets:
        try:
            old, new, changed = _optimize_image(target)
            before += old
            after += new
            optimized += int(changed)
        except Exception as exc:
            errors.append({"file": os.path.basename(target), "error": str(exc)})
    return jsonify({"ok": not errors, "processed": len(targets) - len(errors), "optimized": optimized,
                    "bytesBefore": before, "bytesAfter": after, "errors": errors})


def _convert_image(path, fmt):
    ext = {"jpg": ".jpg", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif", "tiff": ".tiff", "bmp": ".bmp"}[fmt]
    output = os.path.splitext(path)[0] + ext
    if os.path.realpath(output) == os.path.realpath(path):
        return None
    if os.path.exists(output):
        output = os.path.splitext(path)[0] + "_converted" + ext
    if os.path.exists(output):
        output = os.path.splitext(path)[0] + f"_converted_{int(time.time() * 1000)}" + ext
    temp_path = None
    try:
        with Image.open(path) as source:
            animated = fmt == "gif" and getattr(source, "is_animated", False)
            frames = [frame.copy() for frame in ImageSequence.Iterator(source)] if animated else []
            image = frames[0] if frames else source.copy()
            if fmt in {"jpg", "jpeg"}:
                if image.mode not in ("RGB", "L"):
                    if "A" in image.getbands():
                        background = Image.new("RGB", image.size, (255, 255, 255))
                        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
                        image = background
                    else:
                        image = image.convert("RGB")
            with tempfile.NamedTemporaryFile(dir=os.path.dirname(output), suffix=ext, delete=False) as tmp:
                temp_path = tmp.name
            save_args = {}
            if animated:
                save_args.update(save_all=True, append_images=frames[1:], duration=source.info.get("duration", 100), loop=source.info.get("loop", 0))
            image.save(temp_path, format={"jpg": "JPEG", "jpeg": "JPEG", "tiff": "TIFF"}.get(fmt, fmt.upper()), **save_args)
        os.replace(temp_path, output)
        temp_path = None
        return output
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@gallery_bp.post("/convert")
def convert_media():
    data = request.get_json(silent=True) or {}
    fmt = str(data.get("format", "")).lower()
    if fmt not in {"jpg", "jpeg", "png", "webp", "gif", "tiff", "bmp"}:
        return jsonify({"error": "Unsupported output format"}), 400
    path = _operation_target(data.get("path")) if data.get("path") else None
    folder = _operation_target(data.get("folder")) if "folder" in data else None
    if path is None and folder is None:
        return jsonify({"error": "path or folder is required"}), 400
    if folder is not None and not os.path.isdir(folder):
        return jsonify({"error": "Folder not found"}), 404
    targets = [path] if path else [
        _operation_target(os.path.relpath(os.path.join(folder, name), os.path.realpath(resolve_browse_root())))
        for name in sorted(os.listdir(folder))
        if os.path.isfile(os.path.join(folder, name)) and os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS
    ]
    targets = [target for target in targets if target]
    if path and (not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in IMAGE_EXTENSIONS):
        return jsonify({"error": "Image not found or format is unsupported"}), 404
    converted = skipped = deleted = 0
    errors = []
    for source in targets:
        if os.path.splitext(source)[1].lower() == "." + fmt or (fmt == "jpg" and os.path.splitext(source)[1].lower() == ".jpeg"):
            skipped += 1
            continue
        try:
            output = _convert_image(source, fmt)
            if not output:
                skipped += 1
                continue
            converted += 1
            if data.get("delete"):
                os.remove(source)
                deleted += 1
        except Exception as exc:
            errors.append({"file": os.path.basename(source), "error": str(exc)})
    return jsonify({"ok": not errors, "converted": converted, "skipped": skipped, "deleted": deleted,
                    "format": fmt, "errors": errors})
