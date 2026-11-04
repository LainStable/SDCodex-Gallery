# Gallery Plugin

## Overview

The **Gallery** plugin is SDCodex's local media browser. It exposes a Flask
blueprint that serves a browsable, sortable library of images and videos from a
mounted media root, plus a vendored FolderFrame browser front end (see
[`legacy-ui/`](./legacy-ui/EGM.md)).

The plugin is split across three Python modules:

- `plugin.py` — the plugin entry point. Registers the blueprints on the host
  Flask app and mounts the `/gallery2/api` compatibility aliases.
- `gallery_backend.py` — the local-disk library: browsing, metadata, captions,
  workflow export, optimize and convert.
- `immich_backend.py` — the optional Immich bridge: remote servers, albums,
  assets, thumbnails and originals proxied through the plugin.

## Interface

### `plugin.py`

- `init_plugin(app, db, plugin_info=None)` — registers the `gallery_bp`,
  `gallery_immich` and `gallery2_compat` blueprints on the host Flask app and
  returns `True`.
- Compatibility aliases — the `gallery2_compat` blueprint (url_prefix `/gallery2/api`)
  re-exposes `/status`, `/folders`, `/media`, `/file/<path:relpath>`, `/meta`,
  `/caption` (POST), `/workflow`, `/rescan` (POST), `/export-workflows`,
  `/caption-target`, `/prompt`, `/browse-folders` and `/browse-roots`.

### `gallery_backend.py` — blueprint `gallery_bp` (url_prefix `/gallery/api`)

- `legacy_ui_asset` — `GET /legacy-ui/<path:filename>`.
- `folderframe_config` — `GET /folderframe.config.json`.
- `optimize_status_compat` — `GET /optimize-status`.
- `folderframe_browse` — `GET /browse/` and `GET /browse/<path:relpath>`.
- `picker_list` — `GET /picker/list`.
- `picker_file` — `GET /picker/file`.
- `gallery_status` — `GET /status` (optional `counts` argument).
- `list_folders` — `GET /folders`.
- `list_media` — `GET /media` (arguments `folder`, `sort` ∈
  {date_desc,date_asc,size_desc,name_asc}, `filter_workflow`, `filter_sidecar`,
  `filter_4k`).
- `get_media_file` — `GET /file/<path:relpath>`.
- `get_media_meta` — `GET /meta`.
- `save_caption` — `POST /caption`.
- `download_workflow` — `GET /workflow`.
- `rescan_cache` — `POST /rescan`.
- `caption_target` — `GET /caption-target`.
- `get_prompt` — `GET /prompt`.
- `browse_folders` — `GET /browse-folders`.
- `browse_roots` — `GET /browse-roots`.
- `export_workflows` — `GET /export-workflows`.
- `optimize_media` — `POST /optimize`.
- `convert_media` — `POST /convert`.
- Public helpers — `require_login`, `resolve_browse_root`, `is_safe_path`,
  `resolve_file`, `extract_comfy_workflow`, `read_sidecar_caption`,
  `read_embedded_caption`, `read_prompts_and_settings`, `guess_model_badge`,
  `format_bytes`.
- Constants — `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`, `MEDIA_EXTENSIONS`,
  `PICKER_IMAGE_EXTENSIONS`, `EXIF_FIELDS`.

### `immich_backend.py` — blueprint `immich_bp` (url_prefix `/gallery/api/immich`)

- `list_servers` — `GET /servers`.
- `save_server` — `POST /servers`.
- `delete_server` — `DELETE /servers/<sid>`.
- `test_server` — `POST /servers/<sid>/test`.
- `clear_cache` — `POST /cache/clear` (optional `?sid=`).
- `albums` — `GET /<sid>/albums`.
- `assets` — `GET /<sid>/assets?album=`.
- `browse` — `GET /<sid>/browse/` and `GET /<sid>/browse/<path:relpath>`.
- `original` — `GET /<sid>/original/<path:relpath>`.
- `thumb` — `GET /<sid>/thumb/<path:relpath>`.
- `meta` — `GET /<sid>/meta?folder|album=&file=`.
- Public helpers — `config_dir`, `settings_path`, `find_server`,
  `enabled_servers`, `invalidate_cache`.
- Constants — `SETTINGS_FILENAME`, `CONFIG_ENV_VARS`, `DIR_ENV_VARS`,
  `REQUEST_TIMEOUT`, `LISTING_TTL`, `PAGE_SIZE`, `MAX_PAGES`, `CHUNK`,
  `IMAGE_EXTENSIONS`, `VIDEO_EXTENSIONS`.
- `ImmichError` — the exception carrying `.status`, raised for failed Immich calls.

### Subdirectories

- [`legacy-ui/`](./legacy-ui/EGM.md) — the vendored FolderFrame browser front end.

## Invariants

- `gallery_backend.py:_gallery_auth_guard` — every `/gallery/api` request passes
  through the `before_request` hook into `require_login` before the handler runs.
- `gallery_backend.py:_core_session_missing` — a 401 is returned only when the
  core `sdcodex.auth` module is importable and `current_user()` is `None`;
  standalone deployments run unrestricted.
- `plugin.py:init_plugin` — the `/gallery2` compatibility blueprint attaches
  `require_login` as its own `before_request` (`g2.before_request(require_login)`).
- `plugin.py:init_plugin` — blueprint registration is idempotent, guarded by the
  `"gallery_bp" not in app.blueprints`, `"gallery_immich" not in app.blueprints`
  and `"gallery2_compat" not in app.blueprints` checks.
- `plugin.py:init_plugin` — a failure of the Immich bridge is tolerated (the
  try/except registers nothing and logs a warning); the gallery and `/gallery2`
  mounts still proceed.
- `gallery_backend.py:is_safe_path` — containment is enforced via a realpath
  `commonpath` equality with the resolved base (a `ValueError` returns `False`).
- `gallery_backend.py:folderframe_browse` — browse targets outside the root are
  rejected with 403 before the dir / file dispatch.
- `gallery_backend.py:resolve_file` — a file is only served if `is_safe_path`
  holds **and** the target is an existing regular file, else `None`.
- `gallery_backend.py:list_folders` / `gallery_backend.py:list_media` /
  `gallery_backend.py:export_workflows` — `is_safe_path` plus an isdir check
  yields 404 "Folder not found" for traversal or missing folders.
- `gallery_backend.py:_operation_target` — optimize / convert targets are
  realpath-joined to the root and rejected (`None`) when outside it.
- `gallery_backend.py:_picker_join` — picker paths outside the chosen root
  return `(None, rel)` and the callers treat that as 404.
- `gallery_backend.py:list_media` / `gallery_backend.py:export_workflows` /
  `gallery_backend.py:convert_media` — the extension allow-lists gate what is
  listed or processed: `MEDIA_EXTENSIONS` for listings and counts,
  `IMAGE_EXTENSIONS` for export / convert targets.
- `gallery_backend.py:_optimize_image` — only `{jpg,jpeg,png,webp}` inputs are
  accepted (a `ValueError` otherwise) and the original is replaced only when
  `optimized_size < source_size`.
- `gallery_backend.py:_convert_image` — the conversion is a no-op (returns
  `None`) when the output realpath equals the source realpath; existing outputs
  are disambiguated with `_converted` and then `_converted_<ms>`.
- `gallery_backend.py:convert_media` — the output `format` must be in the
  hard-coded allow-list `{jpg,jpeg,png,webp,gif,tiff,bmp}` or the request is
  rejected with 400; same-format sources are skipped, not re-encoded.
- `gallery_backend.py:_picker_count` — the recursive image counting is capped at
  `_PICKER_WALK_LIMIT` (4000) and skips dotdirs, `__pycache__` and `node_modules`.
- `gallery_backend.py:picker_list` — the response is bounded to `folders[:400]`
  and `images[:600]`; hidden entries are skipped.
- `gallery_backend.py:list_folders` / `gallery_backend.py:list_media` — hidden
  dirs and `__pycache__` / `node_modules` are excluded from the folder listings
  and from the media walk.
- `gallery_backend.py:_meta_cache` — the cached metadata is keyed by
  `(full_path, mtime)`, so an mtime change invalidates the entry implicitly.
- `gallery_backend.py:rescan_cache` — the only explicit cache invalidation path
  is clearing `_meta_cache` via `POST /rescan`.
- `gallery_backend.py:gallery_status` — the full library walk
  (`total_files` / `total_folders`) runs only when `counts` is 1 / true / yes.
- `gallery_backend.py:resolve_browse_root` — `GALLERY_ROOT` / `GALLERY` wins
  even when the mount is absent (no silent fallback to another folder).
- `gallery_backend.py:_workspace_root` — the workspace detection walks at most
  5 parent levels for a directory containing both `plugins/` and `backend/`,
  else it falls back to two levels above the plugin directory.
- `gallery_backend.py:browse_folders` — only directories are listed (max 500); a
  non-directory path is reduced to its dirname instead of failing.
- `gallery_backend.py:_read_exif` — only tags in `EXIF_FIELDS` are surfaced;
  byte values and empty / >120-character strings are dropped.
- `gallery_backend.py:extract_comfy_workflow` — the returned metadata must parse
  as a JSON dict / list (`extract_json_if_valid`), otherwise `None`.
- `gallery_backend.py:get_media_meta` — video files skip the embedded-caption,
  SD prompt and workflow extraction; the image branches are gated on `not is_video`.
- `gallery_backend.py:_picker_root` — any readable absolute directory is
  intentionally allowed as a picker root (documented as acceptable because the
  core already exposes `/api/files`).
- `gallery_backend.py:_docker_default_gateway` — falls back to `172.17.0.1` when
  `/proc/net/route` cannot be parsed.
- `immich_backend.py:_immich_auth_guard` — the `before_request` guard denies every
  Immich route (reads and proxied originals included) with 401 when
  `_write_blocked()` is true.
- `immich_backend.py:_write_blocked` — a deny-only auth check: it returns `False`
  when the core `sdcodex.auth` module is unavailable (standalone deployment) and
  `True` when `current_user()` is `None` or raises.
- `immich_backend.py:save_server` / `delete_server` / `test_server` — the write
  routes re-check `_write_blocked()` and return 401 before mutating.
- `immich_backend.py:_public` — the stored API key is never returned by any
  endpoint; only `hasKey` is exposed.
- `immich_backend.py:_save_settings` — the settings are written atomically to
  `<path>.tmp` and then `os.replace`d, with `chmod 0600`.
- `immich_backend.py:_load_settings` — rows without `id` or `url` are dropped;
  `url` is right-stripped of `/`; `enabled` / `verify_tls` default to `True`
  unless explicitly `False`.
- `immich_backend.py:save_server` — a non-empty name and a URL matching
  `^https?://[^\s]+$` are required; a trailing `/api` is stripped; a new server
  without `apiKey` is rejected (400); omitting `apiKey` on an update keeps the
  stored key.
- `immich_backend.py:_fetch_album_assets` — the asset paging is hard-capped at
  `MAX_PAGES` (8) of `PAGE_SIZE` (1000) and stops early on a short page or when
  `total` is reached.
- `immich_backend.py:_cache_get` — cache entries are `(expiry_epoch, value)` and
  are only returned while unexpired; the TTL defaults to `LISTING_TTL`.
- `immich_backend.py:invalidate_cache` — the cache is invalidated per server id
  on save / delete and globally or by `sid` on `/cache/clear`.
- `immich_backend.py:_clean_name` — names are sanitized: `/ \ NUL CR LF TAB` map
  to `_`, whitespace collapses, leading dots / spaces are stripped and a
  fallback is applied when empty.
- `immich_backend.py:_display_name_map` — duplicate display names are
  de-duplicated case-insensitively with a ` (2)`, ` (3)` … extension-preserving suffix.
- `immich_backend.py:_is_displayable` — only assets whose extension is in
  `IMAGE_EXTENSIONS` or `VIDEO_EXTENSIONS` are listed.
- `immich_backend.py:_listing_html` — directory entries always end their href
  with `/` (the gallery only treats trailing-slash URLs as folders); the names
  are URL-quoted and the labels HTML-escaped.
- `immich_backend.py:browse` — a path without a trailing slash that resolves to
  an album is still rendered as a folder listing; an unknown album yields an
  empty listing and an unknown asset gives a 404 plain-text response.
- `immich_backend.py:_index_for` — an unknown server id raises
  `ImmichError(..., 404)`.
- `immich_backend.py:_json_call` — a non-JSON or empty-body response raises
  `ImmichError`; an empty body returns `None`; a non-JSON body raises with status 502.
- `immich_backend.py:_fetch_album_assets` — `/search/metadata` failures with
  status 400 / 404 / 405 / 500 / 501 fall back to the inline `/albums/<id>`
  assets; other statuses propagate.
- `immich_backend.py:_probe_identity` — it tries `/users/me` then `/albums`,
  skipping only 404 / 405; any 401 / 403 means a rejected key.
- `immich_backend.py:test_server` — a `/server/version` 404 / 405 falls back to
  `/server-info/version`; a 401 / 403 is reported as a proxy / WAF block (the
  version endpoint needs no key), and the result is persisted into `last_check`.
- `immich_backend.py:_stream_asset` — the requested Immich `size` thumbnail is
  tried first and only falls through to `/original` on 400 / 404; other errors propagate.
- `immich_backend.py:_open` — `verify_tls` `False` disables TLS verification; the
  `x-api-key` header is always replaced with the stored key.
- `immich_backend.py:_passthrough` — the incoming `Range` header is forwarded and
  `Content-Length` / `Content-Range` / `Accept-Ranges` / `Last-Modified` / `ETag`
  are copied; `Cache-Control` is forced to `private, max-age=120`;
  `attachment=True` yields `Content-Disposition: inline; filename*=UTF-8''…`.
- `immich_backend.py:_passthrough` — the upstream response is always closed in
  the generator's `finally`.
- `immich_backend.py:_settings_lock` — all read-modify-write settings operations
  are serialized under `_settings_lock`; the cache access uses `_cache_lock`.

## Side Effects

- `plugin.py:init_plugin` — mutates `sys.path` by inserting the plugin directory
  at import time.
- `plugin.py:init_plugin` — app mutation: registers the blueprints on the Flask
  app and writes info / warning lines via `app.logger`.
- `gallery_backend.py:save_caption` — filesystem write: it overwrites `<stem>.txt`
  next to the media file with the stripped caption.
- `gallery_backend.py:_optimize_image` — writes a tempfile in the image's
  directory via `tempfile.NamedTemporaryFile(delete=False)` and then `os.replace`s
  it over the original; the temp file is deleted in `finally`.
- `gallery_backend.py:_convert_image` — writes a tempfile next to the output and
  `os.replace`s it to the new file; leftover temp files are deleted in `finally`.
- `gallery_backend.py:convert_media` — filesystem delete: `os.remove(source)` for
  every converted source when the JSON body sets `delete`.
- `gallery_backend.py:resolve_browse_root` — filesystem write:
  `os.makedirs(fallback, exist_ok=True)` creates `<workspace>/downloads` when no
  candidate root exists.
- `gallery_backend.py:list_media` — filesystem reads (`os.walk` / `listdir` /
  `stat`) plus a PIL `Image.open(...)` whose `img.size` and `img.info` give the
  dimensions and the workflow detection, and a mutation of the shared
  `_meta_cache` dict. The image is not decoded with `.load()`.
- `gallery_backend.py:rescan_cache` — mutation of the module-global `_meta_cache`
  via `_meta_cache.clear()`.
- `gallery_backend.py:gallery_status` — a full recursive `os.walk` of the library
  root when `counts=1`.
- `gallery_backend.py:list_folders` — a recursive `os.walk` of every subfolder to
  compute the media counts.
- `gallery_backend.py:folderframe_browse` — `send_file` of arbitrary in-root files
  (disk read / stream) and `os.scandir` for the HTML listings.
- `gallery_backend.py:get_media_file` — a disk read / stream via `send_file`,
  setting `Cache-Control: public, max-age=86400`.
- `gallery_backend.py:picker_file` — a PIL decode and a WEBP re-encode (quality
  82) into an in-memory buffer, or a `send_file` of the raw bytes; `max_age=300`.
- `gallery_backend.py:get_media_meta` — disk reads: `read_sidecar_caption`
  (open `.txt`), a PIL `Image.open(...)` whose `img.size` gives the dimensions,
  a Pillow `getexif()` read via `_read_exif(img)`, and `os.stat`. The image is
  not decoded with `.load()`, and the piexif parse only runs inside
  `read_embedded_caption`, which is skipped when a `.txt` sidecar caption
  exists.
- `gallery_backend.py:download_workflow` — a disk read and a `Response` download
  with a `Content-Disposition` attachment.
- `gallery_backend.py:export_workflows` — a recursive disk walk, PIL metadata
  reads for every image and an in-memory zip construction (`zipfile.ZipFile` over
  a `BytesIO`) returned as an attachment.
- `gallery_backend.py:optimize_media` — iterates the targets calling
  `_optimize_image`, which rewrites files on disk in place.
- `gallery_backend.py:browse_folders` — `os.listdir` / `os.path.isdir` filesystem
  probes and an `os.path.exists("/.dockerenv")` check.
- `gallery_backend.py:browse_roots` — filesystem existence probes (`os.path.isdir`)
  over `/data`, `/data/downloads`, `/data/tasks`, `/data/config`, `~`, `/app` and `/`.
- `gallery_backend.py:_docker_default_gateway` — reads `/proc/net/route` (and
  imports socket / struct) when called.
- `gallery_backend.py:_in_docker` — reads the `REMBG_OUTPUT` environment variable
  and stats `/.dockerenv`.
- `gallery_backend.py:resolve_browse_root` — reads the env vars `GALLERY_ROOT` and
  `GALLERY`; `os.access(..., os.W_OK)` probes on the candidate roots.
- `gallery_backend.py:_picker_roots` — reads the env vars `AI_RENAMER_ROOT` and
  `MODELS_DIR`, plus `os.path.realpath` / `isdir` probes.
- `gallery_backend.py:_folderframe_sources` — imports the sibling
  `immich_backend` module and calls `enabled_servers()` to build the sources; an
  `ImportError` or any exception is swallowed.
- `gallery_backend.py:read_prompts_and_settings` — a disk read via
  `sd_prompt_reader.ImageDataReader` or PIL, parsing the PNG `parameters` /
  `Description` / `prompt` info.
- `gallery_backend.py:_picker_count` — a recursive `os.walk` (bounded) of the
  target folder for counting.
- `gallery_backend.py:read_embedded_caption` — a piexif EXIF load and a PIL image
  metadata read.
- `immich_backend.py:_load_settings` — reads the settings JSON from
  `settings_path()` (exceptions silently yield an empty store).
- `immich_backend.py:_save_settings` — writes the settings JSON file (atomic tmp
  + `os.replace`, mode 0600).
- `immich_backend.py:config_dir` — creates the config directories with
  `os.makedirs(..., exist_ok=True)`.
- `immich_backend.py:_open` — outbound HTTP(S) requests to the configured Immich
  server (`<url>/api<path>`) with the stored API key.
- `immich_backend.py:_json_call` — outbound JSON API calls (albums, metadata
  search, server version, `users/me`, album detail).
- `immich_backend.py:_fetch_albums` — a network read of `/albums` (via the listing cache).
- `immich_backend.py:_fetch_album_assets` — network reads of `/search/metadata`
  and the fallback `/albums/<id>`.
- `immich_backend.py:_probe_identity` — network reads of `/users/me` or `/albums`.
- `immich_backend.py:test_server` — network reads of `/server/version` or
  `/server-info/version`, then a settings write of `last_check`.
- `immich_backend.py:_cache_put` — mutates the module-level `_cache` dict under `_cache_lock`.
- `immich_backend.py:invalidate_cache` — deletes cache entries under `_cache_lock`.
- `immich_backend.py:_stream_asset` — opens an upstream response (the size
  thumbnail or `/original`) and streams it.
- `immich_backend.py:_passthrough` — reads the upstream body in `CHUNK`-sized
  pieces and closes the upstream handle.
- `immich_backend.py:save_server` — appends / overwrites a server row (new ids
  from `secrets.token_hex(4)`) and persists the settings.
- `immich_backend.py:delete_server` — removes the server row and persists the settings.
- `immich_backend.py:test_server` — stamps `last_check` using
  `time.strftime(..., time.gmtime())`.
- `immich_backend.py:_plugin_dir` — reads the filesystem to derive the plugin /
  workspace path (`os.path.isdir` probes).

## Verification

- Run: `<test-command>`
