"""SDCodex Gallery backend — disk-backed browsing, metadata extraction, and workflow export."""

import io
import json
import mimetypes
import os
import re
import time
import zipfile
from urllib.parse import unquote
from flask import Blueprint, Response, jsonify, request, send_file
from PIL import Image

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

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif",
    ".avif", ".heic", ".heif", ".jfif",
}

_meta_cache = {}  # (path, mtime) -> dict
_thumb_cache_dir = None


def _plugin_dir():
    return os.path.dirname(os.path.abspath(__file__))


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
    """Pick first existing directory from GALLERY_ROOT, GALLERY, or common paths."""
    ws = _workspace_root()
    env_root = os.environ.get("GALLERY_ROOT") or os.environ.get("GALLERY")
    if env_root and os.path.isdir(env_root):
        return os.path.abspath(env_root)

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
                    if ext in IMAGE_EXTENSIONS:
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

    folders = []
    # Count root files
    root_count = 0
    try:
        for item in os.listdir(root):
            p = os.path.join(root, item)
            if os.path.isfile(p) and os.path.splitext(item)[1].lower() in IMAGE_EXTENSIONS:
                root_count += 1
    except Exception:
        pass

    folders.append({
        "path": "",
        "name": "All Media",
        "count": root_count,
        "is_root": True,
    })

    # Subdirectories
    try:
        for entry in sorted(os.listdir(root)):
            p = os.path.join(root, entry)
            if not os.path.isdir(p) or entry.startswith(".") or entry in ("__pycache__", "node_modules"):
                continue
            count = 0
            for sub_root, _, files in os.walk(p):
                for f in files:
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
                        count += 1
            folders.append({
                "path": entry,
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
                if os.path.isfile(full) and os.path.splitext(item)[1].lower() in IMAGE_EXTENSIONS:
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
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS:
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

    caption = read_sidecar_caption(target)
    sd_info = read_prompts_and_settings(target)
    wf_json = extract_comfy_workflow(target)

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
