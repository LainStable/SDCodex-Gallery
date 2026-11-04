# legacy-ui (FolderFrame Gallery Browser)

## Overview

The vendored FolderFrame media-browser front end. It is plain ES2020 JavaScript
(no build step, no framework) and is served by the plugin backends from
`<plugin_dir>/legacy-ui`. Three modules make up the app:

- `app.js` — the browser itself: source rail, folder grid, virtualized tile
  window, full-screen viewer, slideshow, zoom/pan/rotate gestures, details
  panel, optimize/convert actions and the star store.
- `settings.js` — configuration and URL handling. Exposes the global
  `window.FolderFrameSettings` (also `module.exports` under Node) with the
  config normalizer, the settings resolver and the URL builders.
- `resilience.js` — shared bounded asynchronous work. Exposes the global
  `window.FolderFrameResilience` (also `module.exports` under Node) with the
  abortable request wrapper, the priority task queue and the bounded image pool.

`folderframe.config.json` is the shipped configuration: one `photos` source plus
`defaults` / `index` / `embed` setting layers. `styles.css` holds the
stylesheet, and `heic2any.min.js` is the vendored HEIC decoder loaded on demand.

## Interface

### `app.js`

- Module-level collaborators
  - `settingsApi` — the `window.FolderFrameSettings` contract consumed by
    `app.js`: `normalizeConfig`, `resolveSettings`, `thumbnailUrl`,
    `relativeMediaPath`, `directoryUrl`, `mediaUrl`.
  - `resilience` — the `window.FolderFrameResilience` contract consumed by
    `app.js`: `createTaskQueue`, `request`, `abortError`.
- Public constants
  - `GRID_DENSITY_PX` — density contract `compact|comfortable|spacious` →
    130 / 180 / 240 px, consumed by `applyGridDensity`.
  - `IGNORED_DIRECTORY_NAMES` — shared ignore-list contract, also used by
    `validateManifestRecord`.
  - `DIRECTORY_TIMEOUT` (15000), `MEDIA_TIMEOUT` (30000), `GRID_BATCH_SIZE` (100),
    `GRID_WINDOW_SIZE` (300), `SCAN_SIBLING_CONCURRENCY` (5),
    `NATIVE_VIEWER_IMAGE_CONCURRENCY` (2), `NATIVE_THUMBNAIL_IMAGE_CONCURRENCY` (12),
    `VIDEO_THUMBNAIL_TIMEOUT` (8000), `ALBUM_COVER_MAX_DEPTH` (4),
    `ALBUM_COVER_MAX_LISTINGS` (12), `DATE_CACHE_TTL`, `DATE_CACHE_LIMIT` (2000),
    `SCAN_MANIFEST_VERSION` (2), `SCAN_MANIFEST_DIRECTORY_LIMIT` (5000),
    `PERSISTENT_MANIFEST_VERSION` (1), `NATIVE_IMAGE_PRIORITY`.
- Window message API
  - `applyAppTheme` — `window.addEventListener('message')` accepts
    `{type:'sdcodex:theme', theme:{mode, vars:{bg, card, ink, inkMuted, inkFaint, primary, secondary}}}`.
  - the same listener accepts `{type:'sdcodex:set-source', sourceId}` to switch
    the gallery source through the URL query.
  - `renderSourceRail` broadcasts `{type:'sdcodex:gallery-sources', activeSourceId, sources}`
    to `window.parent`.
- Storage contracts
  - `ensurePersistentManifest` — published-media-index contract
    `{version, root, chunks, generatedAt?, errors?}` JSON at `activeSource.manifestUrl`.
  - `loadPersistentChunk` — chunk contract
    `{version, root, directories:{path: record}}`, where `record = {path, files[], folders[]}`.
  - `getScanManifest` — localStorage key
    `folderframe.scan-manifest:v<SCAN_MANIFEST_VERSION>:<baseUrl>:<sourceId>:<sourceUrl>`.
  - `loadDateCache` — localStorage key
    `folderframe.date-cache:v1:<baseUrl>:<sourceId>:<sourceUrl>` (array of `[url, {date,size,checked}]`).
  - `ensureStarredSet` — localStorage `folderframe.starred` = `{version:1, paths:[relativePath]}`.
  - `savePreferences` — localStorage row `{album, interval, imageMode, shuffle, autoRefresh, view, sort, gridDensity}`
    under `preferenceKey` from `settingsApi.resolveSettings`.
- HTTP endpoint contracts
  - `requestOptimization` — `POST /gallery/api/optimize` →
    `{optimized|processed, bytesBefore, bytesAfter, errors[]}`; honours the
    `civitaiApiKey` localStorage value as `Authorization: Bearer <token>`.
  - `requestConvert` — `POST /gallery/api/convert` →
    `{converted|processed, format, skipped, deleted, errors[]}`; formats
    `jpg|png|webp|gif|tiff|bmp`.
  - `loadCaptionCapability` — optional-plugin probe `/comfy-caption/api/status`
    then `/api/plugins` (plugin ids `comfy-caption` / `caption`).
  - `loadOptimizeCapability` — capability probe `/gallery/api/optimize-status` →
    `{enabled, convert}`.
  - `loadConfiguration` — `GET /gallery/api/folderframe.config.json`, normalized by
    `settingsApi.normalizeConfig`.
  - `loadMediaMetadata` — `/gallery/api/meta` and `/gallery/api/immich/<sid>/meta`
    → `{ok, size, size_fmt, format, width, height, kind, exif, caption, sdPrompt,
    sdNegative, sdSetting, hasWorkflow, error}`.
  - `captionCurrentImage` — `/gallery/api/caption-target`,
    `/comfy-caption/api/check-connection`, `/comfy-caption/api/caption-single`.
  - `downloadCurrentWorkflow` — `/gallery/api/workflow` for the current metadata file.
- Media helpers
  - `isImageFile` — extension contract `jpe?g|png|webp|gif|avif|bmp|heic|heif`;
    `isVideoFile` — `mp4|mov|webm|m4v`; `isMediaFile` — the union.
  - `infoRelPath` — the `{folder, file}` pair convention used by the metadata and
    caption endpoints.
  - `formatBytes` — public formatter B / KiB / MiB.
  - `imageClipboardAvailable` — gate for the copy UI (requires a secure context,
    `navigator.clipboard.write` and `ClipboardItem`).
  - `detectContainer` — returns `'jpeg'|'png'|'webp'|'gif'|'avif'|'heic'|'quicktime'|'unknown'`
    from magic bytes / ISO-BMFF brands.
  - `reclassifiedContainerError` — builds a `MediaReclassificationError` carrying
    `{container, data}`; `isQuickTimeReclassification` tests `container === 'quicktime'`.
  - `sniffContainer` — returns `{container, data}` by Range-fetching the first 64 bytes.
  - `ascii` — decodes a byte range to a string; `mimeForFormat` — maps a format to
    an `image/*` MIME or `''`.
  - `makeThumbnail` — returns a JPEG blob scaled to max 480 px (quality 0.8).
  - `loadHeicDecoder` — lazily loads `/gallery/api/legacy-ui/heic2any.min.js` and
    resolves `globalThis.heic2any`.
  - `getImagePool` — lazily creates and returns the shared `resilience` image pool.
  - `getSpecialImageURL` — acquires a pool lease and returns its object URL.
  - `scanDirectory` — returns `{filePaths, folderNames, ignored}` for one folder
    using manifest / cache / HTML listing.
  - `scanDirectoryRecursive` — returns `{files, failedFolders}` for a whole tree
    via a bounded breadth-first worker pool.
  - `compareFilenames` — orders URLs by decoded basename (numeric,
    case-insensitive) with a full-URL tie-break.
  - `sortMediaFiles` — returns files sorted by filename or file modification date
    (`'newest'` / `'oldest'`).
  - `cycleSort` — advances `sortMode` through newest → oldest → filename and rescans.
  - `cycleGridDensity` — advances `gridDensity` through compact → comfortable →
    spacious and re-renders.
  - `showScanFailures` — renders the "scan incomplete" notice with the failed folder list.
  - `parentFolder` — the parent path of a folder; `isMissingFolderError` — tests
    HTTPError 404 / 410; `handleMissingFolder` — recovers from a vanished album by
    resetting to its parent and showing the warning overlay.
  - `loadGallery` — main async scan / refresh entry point returning true / false.
  - `setScanStatus`, `setScanProgress`, `setScanProgressBar`, `setMediaLoading` —
    status text and progress-bar writers.
  - `watchThumbnail` — attaches load / error / deadline / cleanup handlers to a
    grid thumbnail element.
  - `showWarning` — shows / hides the warning overlay and configures its
    previous / root / retry / support controls.
  - `isPhotoActive` — true only for an image at `currentIndex` that was not
    reclassified as video.
  - `renderBreadcrumb` — rebuilds the breadcrumb buttons, path bar and grid path label.
  - `navigateToFolder` — async folder navigation with `historyMode`
    `'push'|'replace'|'none'`, returning boolean success.
  - `updateGridView`, `renderGridView` — the grid session and virtualized window.
  - `enterFullScreenViewer`, `showMedia`, `showMediaError`, `scheduleErrorAdvance`,
    `nextMedia`, `prevMedia`, `nextSlideshowMedia` — the viewer.
  - `applyTransform`, `resetZoomAndPan`, `applyFitSize`, `clearFitSize`,
    `applyImageRotation`, `rotateImage`, `toggleImageMode`, `handleWheel` — zoom,
    pan, fit and rotation.
  - `setupEventListeners` — the single registration point for every button and
    viewport / window handler.
  - `startAutoRefreshTimer`, `toggleTvMode` — refresh loop and TV-mode preset.
  - `handleViewerPointerDown`, `handleViewerDismissClick` — click-outside dismissal.
  - `handleMouseDown`, `handleMouseMove`, `handleMouseUp` — drag-pan.
  - `handleTouchStart`, `handleTouchMove`, `handleTouchEnd`, `cancelTouchGesture`,
    `clearDismissVisual` — pinch / drag / swipe-to-dismiss state machine.
  - `toggleSlideshow`, `setSlideshowPlaying`, `stopSlideshow`,
    `cancelSlideshowTimer`, `startSlideshowTimer` — slideshow control API.
  - `handleFullscreenChange`, `updateFullscreenButton`, `toggleFullscreen` —
    fullscreen state sync.
  - `showUI`, `hideUI`, `resetIdleTimer` — chrome visibility and idle-hide.
  - `escapeHtml` — HTML escaper for `& < > " '`.

### `settings.js`

- Exported object (global `window.FolderFrameSettings`, or `module.exports` under
  Node): `{normalizeConfig, resolveSettings, directoryUrl, mediaUrl,
  relativeMediaPath, listingEntry, thumbnailUrl}`.
  `DEFAULTS`, `GRID_DENSITIES`, `BOOLEAN_KEYS`, `SAVED_KEYS`, `INTERVALS`,
  `object`, `validateSettings`, `applyLayer` and `urlSettings` are module-local
  and are **not** exported.
- `normalizeConfig` — config → `{baseUrl, sources, defaults, index, embed}`
  normalization / validation entry point.
- `resolveSettings` — `(config, search, readStored)` →
  `{settings, profile, source, preferenceKey, warnings}`.
- `directoryUrl`, `mediaUrl`, `relativeMediaPath` — URL construction / resolution helpers.
- `listingEntry` — `(href, requestedUrl)` → `{name, directory}` or `null`.
- `thumbnailUrl` — `(source, file)` → webp thumbnail href or `null`.
- Local allow-lists: `GRID_DENSITIES` `['compact','comfortable','spacious']`;
  `INTERVALS` `[3,5,10,15,30,60,300,900,3600]`; plus `BOOLEAN_KEYS`, `SAVED_KEYS`
  and `DEFAULTS`.

### `resilience.js`

- Exported object (global `window.FolderFrameResilience`, or `module.exports`
  under Node): `{request, createTaskQueue, createImagePool, abortError,
  timeoutError, httpError}`.
- `request(url, {signal, timeout=15000, body='text', ...fetchOptions})` —
  abortable, deadline-bounded fetch returning text / json / blob.
- `createTaskQueue({concurrency=4})` → `{schedule(task,{signal,priority}), stats()}`.
- `createImagePool({download, decode, thumbnail, createURL, revokeURL, warn,
  concurrency, decodeTimeout, grace, limits})` →
  `{acquire(file, kind, signal), invalidate(file), stats()}` with kinds
  `viewer` / `thumbnail`.
- `abortError` / `timeoutError` / `httpError` — error factories whose `name` is
  `AbortError` / `TimeoutError` / `HTTPError`; the latter carries `status` and
  `url`. `httpErrorDetailed` is module-local and adds `detail`.

### `folderframe.config.json`

- `sources` — config schema surface:
  `sources[] {id, label, path, thumbnailPath?, manifestPath?, scanCache?, discoveryMode}`
  plus the `defaults`, `index` and `embed` sections (only these four top-level
  keys are accepted).
- `defaults` — the shipped defaults (source `photos`, view `folders`, sort
  `filename`, interval 5, imageMode `fit`, gridDensity `comfortable`,
  refreshInterval 120 index / 300 embed); the `embed` section disables
  `rememberPreferences`.

## Invariants

- `app.js:DIRECTORY_TIMEOUT` — directory / index / metadata requests use 15000 ms.
- `app.js:MEDIA_TIMEOUT` — media requests use 30000 ms.
- `app.js:GRID_BATCH_SIZE` — the grid renders 100 tiles per batch.
- `app.js:GRID_WINDOW_SIZE` — the grid virtualizer window is 300.
- `app.js:SCAN_SIBLING_CONCURRENCY` — at most 5 simultaneous recursive directory listings.
- `app.js:IGNORE_SENTINELS` — `folderframe.ignore` / `.frameignore` mark a directory ignored.
- `app.js:isIgnoredDirectoryName` — a fixed denial list plus the `.trash-*` prefix.
- `app.js:isIgnoredFileName` — excludes dotfiles, `~$` temp files and the
  `.tmp/.part/.partial/.crdownload/.download/.bak/.old` suffixes.
- `app.js:NATIVE_VIEWER_IMAGE_CONCURRENCY` — viewer image loads are limited to 2.
- `app.js:NATIVE_THUMBNAIL_IMAGE_CONCURRENCY` — thumbnail loads are limited to 12.
- `app.js:NATIVE_IMAGE_PRIORITY` — frozen priority map `{grid:0, album:5, viewer:10}`.
- `app.js:videoThumbnailQueue` — video thumbnail loads are limited to 2.
- `app.js:VIDEO_THUMBNAIL_TIMEOUT` — a video thumbnail aborts / errors after 8000 ms.
- `app.js:ALBUM_COVER_MAX_DEPTH` — the cover search recurses at most 4 levels.
- `app.js:ALBUM_COVER_MAX_LISTINGS` — the cover search inspects at most 12 listings.
- `app.js:startAlbumPreviews` — at most 3 album previews load concurrently; the
  observer `rootMargin` is 300 px.
- `app.js:queueNativeImageSource` — forces `loading='eager'` because visibility is
  already gated by the queue / observer.
- `app.js:queueVideoThumbnail` — resets `preload='metadata'` and clears the source
  on error / abort.
- `app.js:safeManifestPath` — rejects non-strings, backslash / control chars and
  `.` / `..` segments; an empty path is allowed only with `allowEmpty`.
- `app.js:validateManifestRecord` — requires `record.path === expectedPath`, both
  `files` / `folders` arrays, valid non-nested folder names and media-only file
  paths whose parent equals `expectedPath`; ignored dirs / files are dropped.
- `app.js:ensurePersistentManifest` — requires
  `index.version === PERSISTENT_MANIFEST_VERSION` (1) and a valid root record;
  failures are memoized via `state.unavailable` for the scan.
- `app.js:loadPersistentChunk` — the chunk URL must stay same-origin, under the
  manifest base pathname and carry no query / hash; failures are cached as `null`
  per scan unless `isManifestOnlySource()`.
- `app.js:listingFromManifest` — only `Number.isFinite` mtime / size are trusted.
- `app.js:SCAN_MANIFEST_VERSION` — scan manifest version 2;
  `SCAN_MANIFEST_DIRECTORY_LIMIT` 5000 entries, trimmed keeping the newest `checked`.
- `app.js:saveScanManifest` — persistence is skipped when `activeSource.scanCache` is false.
- `app.js:DATE_CACHE_TTL` — the modified-date cache TTL is 24 h.
- `app.js:DATE_CACHE_LIMIT` — the modified-date cache is capped at 2000 entries, oldest evicted.
- `app.js:loadDateCache` — cached entries must start with `activeSource.url`;
  malformed / corrupt storage is cleared.
- `app.js:loadConfiguration` — the config fetch aborts after 8000 ms and falls
  back to the defaults with a visible warning.
- `app.js:STARRED_STORAGE_VERSION` — the star store requires `version === 1` and
  an array of non-empty string paths.
- `app.js:openConvertPrompt` — refuses to open while `operationInFlight` or `isScanning`.
- `app.js:optimizeCapable` — the capability defaults to true and is disabled only
  when the server reports `enabled:false` / `convert:false`.
- `app.js:updateControlStates` — the folder Optimize / Convert controls are hidden
  in `starFilterMode`, in `galleryViewMode === 'all'` or for a remote (Immich) source.
- `app.js:captionCurrentImage` — the caption-target and metadata calls use 15000 ms
  timeouts; the caption only runs when `rel.file` resolves.
- `app.js:loadCaptionCapability` — a non-OK / 401 / 404 status leaves the Caption
  section hidden.
- `app.js:updateFolderHistory` — a no-op when `window.history.pushState` is unavailable.
- `app.js:window.addEventListener('message')` — messages whose `event.origin`
  differs from `window.location.origin` are ignored.
- `app.js:renderSourceRail` — the source rail is not rendered inside an iframe
  (`window.parent !== window`).
- `app.js:IGNORE_SENTINELS` — a folder containing `folderframe.ignore` /
  `.frameignore` causes `scanDirectory` to clear all discovered files and folders
  for that directory (`ignored=true`).
- `app.js:SCAN_SIBLING_CONCURRENCY` — the recursive scan concurrency is capped at
  5 in-flight directory listings via a flat frontier plus `pump()`, so a worker
  never waits on its own children (deadlock-free by construction).
- `app.js:scanDirectoryRecursive` — a `visited` Set prevents re-scanning a path;
  once `settled` is true late results are not published; abort throws
  `resilience.abortError`.
- `app.js:scanDirectory` — a cached listing is reused only when the normalized
  path's mtime matches `directoryModifiedTime` and the cached files / folders are
  arrays; cache writes are skipped for `options.bypassCache` or for sources
  without `scanCache`.
- `app.js:sortMediaFiles` — the whole date metadata pass is bounded by an 8000 ms
  `AbortController` timeout and at most 4 concurrent HEAD workers; files without
  dates sort last; cached dates are used unless `refreshDates` or older than
  `DATE_CACHE_TTL`.
- `app.js:compareFilenames` — ordering is numeric + case-insensitive by decoded
  basename with a plain URL compare as tie-break.
- `app.js:cycleSort` — `sortMode` is restricted to the fixed cycle
  `['newest','oldest','filename']` and the cycle is a no-op while `isScanning`.
- `app.js:cycleGridDensity` — `gridDensity` cycles
  `['compact','comfortable','spacious']` and never triggers a rescan or network request.
- `app.js:loadGallery` — only one scan runs at a time (the previous `scanSession`
  is aborted); every await re-checks `current()` (`scanSession === session && !aborted`)
  before publishing.
- `app.js:loadGallery` — a progressive publish fires only when ≥400 files are
  unpublished or ≥2000 ms have elapsed since the previous publish, unless forced;
  the initial preview is capped at 100 files.
- `app.js:loadGallery` — files from `result.failedFolders` are re-added to the
  result instead of being dropped, so an unreadable descendant never deletes
  previously loaded media.
- `app.js:loadGallery` — `starFilterMode` always scans the whole source
  (folder `''`) and filters `mediaFiles` with `isStarred`; subfolders are then emptied.
- `app.js:setScanProgressBar` — the percentage is clamped to 0..100, `null` /
  `NaN` hides the track and clears `aria-valuenow`, and the literal `'busy'`
  renders an indeterminate bar; it is kept separate from the status text writes.
- `app.js:handleMissingFolder` — aborts the scan session, stops the viewer / grid /
  slideshow, clears `mediaFiles` / `subfolders` and sets `missingFolderRecovery` so
  the warning offers a parent-folder recovery.
- `app.js:navigateToFolder` — on a failed load to the requested folder it restores
  `previousFolder`, sets `failedNavigation`, re-renders and returns false.
- `app.js:imageClipboardBlob` — rejects unless `imageReady` and both
  `naturalWidth` / `naturalHeight` are non-zero, and always encodes `'image/png'`.
- `app.js:copyCurrentImage` — requires `window.isSecureContext`,
  `navigator.clipboard.write` and a defined `ClipboardItem`, otherwise it fails
  with "Image clipboard requires a secure context".
- `app.js:updateMediaActions` — remote (Immich) sources hide and disable
  Optimize / Convert, hide the caption section and point Download at
  `immichOriginalUrl`; Optimize requires `isOptimizableImage` (JPEG / PNG) and
  `optimizeCapable`; Convert requires an image, `convertCapable` and no
  `operationInFlight`.
- `app.js:updateGalleryHeaderLayout` — controls are moved into the Options menus
  one-by-one (density, sort, stars, optimize, convert, view-mode) only until
  `gridHeaderMain + gridActions + 18 px` fits within `gridHeader.clientWidth`.
- `app.js:detectContainer` — brand scanning is limited to the first 64 bytes and
  checked in order avif → heic → quicktime, defaulting to `'unknown'`.
- `app.js:makeThumbnail` — the scale is capped at 1 and the longest edge at 480 px,
  encoded as JPEG quality 0.8.
- `app.js:loadHeicDecoder` — the decoder promise is memoized and reset to `null`
  on load failure so a later HEIC request can retry.
- `app.js:getImagePool` — decode accepts only jpeg / png / webp / gif / avif as
  direct blobs, throws a quicktime reclassification error for `'quicktime'`, and
  only HEIC goes through `heic2any` (`toType image/jpeg`, quality 0.92).
- `app.js:getSpecialImageURL` — if the signal is already aborted the acquired lease
  is released and `resilience.abortError()` is thrown.
- `app.js:watchThumbnail` — the fallback chain is
  `element.generatedFallback` → `element.fallbackSrc` → `'Preview unavailable'`;
  deadline expiry uses `MEDIA_TIMEOUT`; callbacks no-op once the grid session is
  aborted or `resourceActive === false`.
- `app.js:showWarning` — the support / help / offline / open-source blocks are
  hidden when the state is a confirmed album / manifest condition (recovery,
  `emptyFolderNotice`, `manifestFailureNotice` or a manifest-only source), and
  Retry is relabelled "Reload Library" for manifest-only sources.
- `app.js:renderGridView` — thumbnail lazy-loading uses `IntersectionObserver` with
  root `gridViewContainer` and `rootMargin` equal to `OBSERVER_LOAD_MARGIN` (300 px),
  kept in sync with the manual `isWithinLoadMargin` check.
- `app.js:renderGridView` — tile activation probes a persistent thumbnail with
  `HEAD` under an `AbortController` aborted after `DIRECTORY_TIMEOUT`; only
  404 / 410 remove the file from `mediaFiles` and `persistentThumbnailUrls` /
  `modifiedDateCache`; every other failure is inconclusive and the viewer still opens.
- `app.js:gridVirtualizer` — virtualization only engages above `GRID_BATCH_SIZE`;
  rendered windows are capped at `GRID_WINDOW_SIZE` and the scroll update
  re-windows only when the first visible row leaves the `GRID_BATCH_SIZE/2` edge
  buffer; the initial window is `GRID_BATCH_SIZE`, or `GRID_WINDOW_SIZE` when
  jumping to a return target.
- `app.js:gridMetrics` — the grid column / row math is pinned to the stylesheet:
  scale `0.667` (≤560 px) / `0.778` (≤900 px) / `1`, gap 12 or 18 and a
  `gridTileMinPx`-derived `minmax` minimum, so spacer heights match the CSS column counts.
- `app.js:session.appendTiles` — appending tiles only grows the DOM when the new
  window tail stays ≤ `start + GRID_WINDOW_SIZE`, otherwise it re-renders the
  window; crossing the `GRID_BATCH_SIZE` threshold forces a full `renderGridView()` rebuild.
- `app.js:showMedia` — the viewer index always wraps:
  `currentIndex = (index + mediaFiles.length) % mediaFiles.length`.
- `app.js:showMedia` — a media load watchdog of `MEDIA_TIMEOUT` is started on load,
  re-armed on buffering / stall / timeupdate progress and cleared on
  pause / canplay / playing; it reports via
  `resilience.timeoutError('Media loading stalled')` only while `isCurrent()` and
  not `mediaFailed`.
- `app.js:showMedia` — every async viewer callback is gated by `isCurrent()`
  (`loadId === mediaLoadId && !isGridViewActive && !session.controller.signal.aborted`),
  and `showMedia` / `showMediaError` call `stopViewerSession()` first so stale
  loads cannot paint.
- `app.js:showMedia` — object URLs created from QuickTime blobs are revoked either
  on abort (once listener) or when the load is no longer current.
- `app.js:scheduleErrorAdvance` — slideshow auto-advance on error is exactly 3000 ms
  and only fires when `slideshowPlaying && mediaFailed && !isGridViewActive` and the
  load id is unchanged.
- `app.js:nextSlideshowMedia` — shuffle advance never re-selects the current file
  (`while (next === currentIndex)`) and falls back to linear next when shuffle is
  off or there is a single file.
- `app.js:handleWheel` / `app.js:handleTouchMove` — zoom is clamped to `minZoom`
  (0.1 in `original`, 1.0 in `fit`) and 10.0 in both the wheel and pinch paths; at
  exactly 1.0 in non-original mode the pan resets to 0.
- `app.js:rotateImage` / `app.js:applyImageRotation` — rotation is normalized
  `(imageRotation + 90) % 360` and requires an active photo and no `mediaFailed`;
  the rotated fit-scale is computed from the natural dimensions, not the element box.
- `app.js:handleWheel` / `app.js:handleMouseDown` / `app.js:handleTouchStart` —
  wheel / pointer / touch gestures all bail out when `!controlsEnabled`,
  `mediaFailed`, or when the current item is not a photo (except in the
  viewer-boundary handling).
- `app.js:handleViewerDismissClick` — click-outside dismissal requires the event
  target to be exactly `viewport`, `container` or `viewerBackdrop`, pointer travel
  ≤ 6 px since mousedown, and no TV mode / grid mode.
- `app.js:handleTouchMove` — the swipe axis is only decided after 10 px of motion
  using a 1.15 dominance ratio; an undecided axis stays undecided until re-evaluated.
- `app.js:handleTouchEnd` — swipe-to-dismiss needs a `'vertical'` axis, a downward
  `dy`, and either `dy >= min(180, max(100, viewportHeight*0.22))` or the quick
  fling rule (`elapsed >= 40 && dy >= 60 && dy/elapsed >= 0.75`); horizontal
  navigation needs `|dx| >= 50` and `|dx| > |dy| * 1.5` and only while
  `imageMode==='fit' && zoom===1 && !panX && !panY`.
- `app.js:resetIdleTimer` / `app.js:hideUI` — the chrome idle-hide delay is 3000 ms
  normally and 1800 ms in TV mode; `hideUI` is suppressed while in the grid,
  dragging, pinching, `mediaFailed`, or when focus-visible is inside
  `#overlay-header`, `.nav-arrow` or `#info-panel`.
- `app.js:setupEventListeners` — keydown handling requires `controlsEnabled`, skips
  `defaultPrevented` / IME / modifier keys, skips editable and
  `a,select,input,textarea,video,[contenteditable]` targets, and only acts on the
  allow-list `arrowleft,arrowright,' ',enter,s,f,t,r,g,escape`; Space / Enter are
  prevented so focused buttons do not activate.
- `app.js:setupEventListeners` — grid keyboard scrolling is limited to
  `Home,End,ArrowUp,ArrowDown,PageUp,PageDown` with 48 px steps and 0.85 viewport
  for the Page keys; the back-to-top button is hidden below 320 px of scroll.
- `app.js:setupEventListeners` — resize work is coalesced through one
  `requestAnimationFrame` (`resizeFrame` guard).
- `app.js:startAutoRefreshTimer` / `app.js:setupEventListeners` — auto-refresh runs
  on `refreshInterval * 1000` and skips when `document.hidden` or
  `operationInFlight`; `visibilitychange` triggers the same guarded silent reload.
- `app.js:toggleTvMode` / `app.js:handleFullscreenChange` — TV mode forces
  `imageMode='fit'`, `shuffleEnabled=true`, `autoRefreshEnabled=true`, starts the
  slideshow, and exiting TV mode pauses the video, clears the progress bar and
  exits fullscreen; leaving fullscreen while in TV mode resets those flags and
  persists the preferences.
- `app.js:startSlideshowTimer` — the slideshow progress is rAF-driven from
  `slideshowStartedAt`, clamped to 100%, and the timer refuses to start unless
  `imageReady && !mediaFailed && !isGridViewActive`.
- `app.js:setupEventListeners` — Convert is refused unless the selected format
  exists in `CONVERT_FORMAT_LABELS`, the file is an image, `convertCapable` and no
  `operationInFlight`; folder-level optimize / convert is refused in starred mode,
  in a non-`'folders'` view, while scanning, or when already in flight.
- `app.js:enterFullScreenViewer` — returns early when `mediaFiles` is empty and only
  auto-opens the info panel on the grid → viewer transition.
- `settings.js:normalizeAlbum` — rejects non-strings, backslashes, control chars
  (`\x00-\x1f`) and `.` / `..` segments, and re-joins with `'/'`.
- `settings.js:validateSettings` — a strict allow-list schema: unknown keys throw,
  booleans must be real booleans, `source` must exist, `view` ∈ {all,folders},
  `sort` ∈ {newest,oldest,filename}, `imageMode` ∈ {fit,original},
  `gridDensity` ∈ `GRID_DENSITIES`, `refreshInterval` an integer 1–86400 and
  `interval` ∈ `INTERVALS`.
- `settings.js:normalizeConfig` — accepts only `sources/defaults/index/embed`;
  source ids must be unique and match `/^[a-zA-Z0-9_-]+$/`, labels must be
  non-empty, paths must be HTTP(S) with no credentials / query / hash (the
  pathname is forced to a trailing `'/'`), `manifestPath` must additionally end
  `.json`, `discoveryMode` ∈ {auto,directory,manifest} and `manifest` requires
  `manifestPath`, `scanCache` must be boolean.
- `settings.js:resolveSettings` — the profile is `embed` only when `?profile=embed`;
  the defaults are `refreshInterval` 120 (index) / 300 (embed) and
  `rememberPreferences` true only for index; a manifest source
  (`discoveryMode !== 'directory' && manifestUrl`) defaults `autoRefresh=false`
  unless a config layer explicitly set it or enabled `tvMode`.
- `settings.js:resolveSettings` — saved preferences are keyed
  `folderframe.preferences:<baseUrl>:<profile>:<source.id>:<source.url>` and only
  `SAVED_KEYS` are read back, re-validated through `validateSettings`; parse /
  validation failures degrade to a warning.
- `settings.js:resolveSettings` — the legacy `gallery.preferences` blob
  (`folder` / `galleryViewMode` → `album` / `view`) is honoured only for the
  original `photos` source in the index profile.
- `settings.js:urlSettings` — booleans must be literally `0` / `1`; the interval is
  coerced with `Number`; invalid / unknown values are skipped and reported through
  the `warnings` array rather than throwing.
- `settings.js:applyLayer` — `tvMode` is a preset (`fit`, shuffle, autoRefresh,
  autoplay) that explicit fields in the same layer override.
- `settings.js:mediaUrl` / `settings.js:directoryUrl` — `mediaUrl` rejects empty
  filenames, names containing `/` or `\`, and the names `.` / `..`;
  `directoryUrl` normalizes the album first.
- `settings.js:listingEntry` — follows only direct children: it rejects `?` / `#`
  hrefs, foreign origins, query strings, fragments, paths outside the requested
  pathname, and children containing `/` or `.` / `..` / control chars.
- `settings.js:thumbnailUrl` — returns `null` when the source has no
  `thumbnailUrl` or the relative path is empty / starts with `../`.
- `settings.js:relativeMediaPath` — only strips the base pathname when the origin
  matches, otherwise it falls back to the last path segment.
- `resilience.js:request` — always races a deadline timer (default 15000 ms) that
  aborts the internal `AbortController` and rejects `TimeoutError`; body
  consumption is inside the deadline; upstream aborts reject `AbortError`
  immediately (including already-aborted signals); non-OK responses reject
  `HTTPError` with the server `detail`; the timer and abort listener are always cleaned up.
- `resilience.js:createTaskQueue` — requires a positive-integer `concurrency`,
  drains `priority` descending then FIFO `order` ascending, rejects tasks
  scheduled on an already-aborted signal, and removes queued items (rejecting with
  `AbortError`) when their signal aborts.
- `resilience.js:createImagePool` — caps concurrency (default 2), applies
  `decodeTimeout` (30000 ms) per job with a `timedOut` rejection, gives orphaned
  queued jobs a 250 ms `grace` window before removal, aborts download-state
  orphans, and never interrupts an in-flight decode by consumer count.
- `resilience.js:createImagePool` — eviction only removes unreferenced entries,
  oldest `used` first, until the per-kind caps (`count`, `bytes`) are met — viewer
  32 entries / 64 MB, thumbnail 128 entries / 16 MB — while stale entries are
  evicted regardless; stale URLs are revoked only when `refs` reaches 0.
- `resilience.js:run` (inside `createImagePool`) — queued pool jobs are ordered
  viewer-consumers before thumbnail-only jobs, and a completed full-resolution
  viewer blob is reused as the thumbnail source when the viewer cache is fresh.
- `resilience.js:acquire` / `resilience.js:pump` — a job already marked `timedOut`
  rejects new `acquire` calls with the HEIC-stalled error, and when every running
  job is timed out all queued jobs are rejected to free the pool.
- `resilience.js:invalidate` — marks matching entries stale (all files when `file`
  is falsy), deletes them from the cache, revokes immediately when unreferenced and
  re-runs eviction.
- `resilience.js:acquire` — rejects unknown variant kinds (`!caches[kind]`) and
  already-aborted signals before touching the queue.
- `folderframe.config.json:defaults` — states the shipped defaults (source `photos`,
  view `folders`, sort `filename`, interval 5, imageMode `fit`, gridDensity
  `comfortable`, refreshInterval 120 index / 300 embed) and the embed section
  disables `rememberPreferences`.

## Side Effects

- `app.js:$` — binds ~95 DOM nodes by id at script load (missing ids become `null`).
- `app.js:applyAppTheme` — mutates `document.documentElement` `dataset.theme`,
  `style.colorScheme` and the `--bg/--card-bg/--text/--text-secondary/--border/--primary/--info/--ff-glow/--ff-glow-hover` variables.
- `app.js:applyGridDensity` — sets the CSS variable `--grid-tile-min` on `document.documentElement`.
- `app.js:renderSourceRail` — `list.replaceChildren()`, creates buttons / spans,
  toggles the `document.body` class `has-source-rail`, `rail.hidden`, and calls
  `window.parent.postMessage`.
- `app.js:renderSourceRail` — `location.assign()` on a source switch; the same in
  the `sdcodex:set-source` message handler.
- `app.js:loadConfiguration` — mutates `btnDownload.hidden`,
  `btnCopyLink.hidden`, `btnCopyFilename.hidden`, the `document.body` classes
  `hide-filenames` / `hide-viewer-button-labels` / `controls-free`, and writes `#config-notice`.
- `app.js:savePreferences` — `localStorage.setItem(preferenceKey, …)`.
- `app.js:getScanManifest` — `localStorage.getItem`; `saveScanManifest` —
  `localStorage.setItem` with `JSON.stringify`.
- `app.js:loadDateCache` — `localStorage.getItem`; `saveDateCache` — `localStorage.setItem`.
- `app.js:ensureStarredSet` — `localStorage.getItem('folderframe.starred')`;
  `saveStarredSet` — `localStorage.setItem`.
- `app.js:directoryModifiedTime` — `fetch(url, {method:'HEAD', cache:'no-store'})`
  plus `AbortController` / `setTimeout`.
- `app.js:requestOptimization` — `fetch('POST /gallery/api/optimize')` with an
  optional Bearer header from `localStorage.getItem('civitaiApiKey')`.
- `app.js:requestConvert` — `fetch('POST /gallery/api/convert')` with an optional
  Bearer header from `localStorage.getItem('civitaiApiKey')`.
- `app.js:loadConfiguration` — `fetch('/gallery/api/folderframe.config.json')`
  with an 8 s `AbortController`.
- `app.js:loadCaptionCapability` — `fetch('/comfy-caption/api/status')` and
  `fetch('/api/plugins')`.
- `app.js:loadOptimizeCapability` — `fetch('/gallery/api/optimize-status')`.
- `app.js:captionCurrentImage` — `fetch('/gallery/api/caption-target')`,
  `fetch('/comfy-caption/api/check-connection')`,
  `fetch('/comfy-caption/api/caption-single')`; reads `localStorage` caption
  settings; mutates `#caption-this-label`, `#caption-this-status` and the button's
  disabled state.
- `app.js:loadMediaMetadata` — `fetch('/gallery/api/meta')` or
  `/gallery/api/immich/<sid>/meta` via `resilience.request` with an
  `AbortController`.
- `app.js:findAlbumCover` — calls `scanDirectory(folder, {signal})` (the plugin's
  HTTP directory listing).
- `app.js:queueNativeImageSource` — sets `element.onload` / `onerror`,
  `element.src`, `element.loading`, `element.folderFrameDecodeToken`;
  `console.error` / `console.warn` on failures.
- `app.js:queueVideoThumbnail` — mutates `element.src` / `preload`, attaches
  `onloadeddata` / `onerror`, `console.warn` on failure.
- `app.js:clearMediaSource` — removes `src` and calls `element.load()`.
- `app.js:stopViewerSession` — `AbortController.abort()`, `clearTimeout`.
- `app.js:stopGridSession` — aborts the controller, disconnects the observer, runs
  the cleanups and aborts the album previews.
- `app.js:startAlbumPreviews` — creates an `IntersectionObserver` rooted at `#grid-view-container`.
- `app.js:loadAlbumPreview` — appends an `.album-cover` `<img>` to the tile and
  toggles `has-album-cover` / `hidden` / `dataset` flags.
- `app.js:updateFolderHistory` — `window.history.pushState` / `replaceState`.
- `app.js:setInfoPanel` — toggles `#info-panel` `hidden` / `ui-hidden` and
  `#btn-info` `aria-pressed`.
- `app.js:showInfoLoading` — writes `textContent` / `hidden` on the info-panel fields.
- `app.js:renderInfoStats` — sets `#info-stats.innerHTML` (string-composed markup)
  and `#info-exif` `textContent`.
- `app.js:renderInfoMeta` — writes the caption / prompt / settings text and toggles
  the workflow section / button.
- `app.js:updateControlStates` — writes many DOM text / title / aria / disabled /
  hidden states and calls `syncPlayButton`.
- `app.js:downloadCurrentWorkflow` — creates an `<a download>`, appends it to the
  body, calls `.click()` and removes it.
- `app.js:updateMediaActions` — sets `btnDownload.href` / `download` / `aria-label`
  and disables rotate / copy.
- `app.js:ensurePersistentManifest` — `resilience.request(manifestUrl, {cache:'no-store'})`;
  `console.info` / `console.warn`.
- `app.js:loadPersistentChunk` — `resilience.request(chunkUrl, {cache:'no-store'})`;
  `console.warn` on fallback.
- `app.js:persistentDirectoryListing` — `console.warn` on an invalid / missing record.
- `app.js:window.addEventListener('DOMContentLoaded')` — sequential startup:
  `loadConfiguration`, capability probes, listeners, `loadGallery`, a history
  replace and the auto-refresh timer.
- `app.js:window.addEventListener('popstate')` — re-navigates to the `album` query parameter.
- `app.js:window.addEventListener('beforeunload')` — aborts `scanSession`, stops the
  viewer / grid sessions and calls `clearImageBlobCache()`.
- `app.js:loadMediaMetadata` — `fetch` through `resilience.request` with an
  `AbortController` (timeout 15000, `cache:'no-store'`), plus DOM writes to the
  info-panel fields.
- `app.js:downloadCurrentWorkflow` — creates an
  `<a href=/gallery/api/workflow?... download rel=noopener>`, appends it to
  `document.body`, clicks it and removes it.
- `app.js:copyCurrentFilename` — `navigator.clipboard.writeText` or
  `document.execCommand('copy')` on a temporary textarea appended to
  `document.body`; `console.warn` on failure.
- `app.js:copyCurrentImage` — `navigator.clipboard.write` + `ClipboardItem`;
  mutates the button label / title and schedules `setTimeout` resets.
- `app.js:imageClipboardBlob` — `document.createElement('canvas')`,
  `getContext('2d')`, `drawImage`, `canvas.toBlob`.
- `app.js:makeThumbnail` — `URL.createObjectURL` / `revokeObjectURL`, `new Image()`,
  canvas `drawImage` / `toBlob`.
- `app.js:loadHeicDecoder` — injects
  `<script src=/gallery/api/legacy-ui/heic2any.min.js>` into `document.head`.
- `app.js:sniffContainer` — `resilience.request` with `Range: bytes=0-63` and
  `cache:'no-store'`.
- `app.js:scanDirectory` — `resilience.request` of the directory URL and
  `DOMParser().parseFromString(html,'text/html')`; calls `saveScanManifest()` to
  persist the scan manifest (localStorage-backed).
- `app.js:scanDirectoryRecursive` — global progress mutation via
  `setScanProgress` / `scannedFolders` / `scannedFiles` and awaits the caller's
  `onBatch` callback.
- `app.js:sortMediaFiles` — `fetch(file, {method:'HEAD', cache:'no-store'})` per
  file to read `Last-Modified`, plus `loadDateCache` / `trimDateCache` /
  `saveDateCache` persistence and a DOM write to the `#btn-sort` title.
- `app.js:cycleSort` — calls `updateControlStates` and awaits `loadGallery` (a full rescan).
- `app.js:cycleGridDensity` — calls `applyGridDensity` / `savePreferences`
  (localStorage) and `renderGridView`.
- `app.js:handleMissingFolder` — aborts `scanSession`, stops the viewer / grid /
  slideshow sessions, pauses and clears the `<video>` / `<img>` sources, mutates the
  viewport / grid displays, the `innerHTML` of `thumbnailGrid` and the warning
  texts, then calls `savePreferences`.
- `app.js:loadGallery` — an `AbortController` per scan plus many DOM writes
  (`aria-busy`, `button.disabled`, `#scan-loading`, the progress bar, the warning
  overlay, the breadcrumb); calls `savePreferences`, `clearImageBlobCache`,
  `renderGridView` / `enterFullScreenViewer`; `console.error` on failure.
- `app.js:setScanStatus` — writes `scanStatus` `textContent` / class and `#scan-loading-text`.
- `app.js:setScanProgressBar` — writes `#scan-progress-track` / `#scan-progress-fill`
  `hidden`, classes, `style.width` and `aria-valuenow`.
- `app.js:setMediaLoading` — writes `#media-loading` `hidden`, `#media-loading-text`
  and the container `aria-busy`.
- `app.js:watchThumbnail` — `setTimeout` deadline, element event-handler assignment,
  `classList` changes, appends a "Preview unavailable" span, and registers the
  cleanup in `gridSession.cleanups`.
- `app.js:renderBreadcrumb` — `innerHTML` clearing and `appendChild` of breadcrumb
  buttons / spans, plus click listeners calling `navigateToFolder` and many
  attribute / text writes.
- `app.js:navigateToFolder` — aborts `scanSession`, stops the viewer / grid /
  slideshow, scrolls `gridViewContainer` to the top, updates the folder history via
  `historyMode` and awaits `loadGallery`.
- `app.js:updateGalleryHeaderLayout` — re-parents DOM buttons between the slots and
  menus and writes role attributes and the `actions-collapsed` class.
- `app.js:renderGridView` — `IntersectionObserver` creation, an `AbortController` per
  tile source, `URL.createObjectURL` / `revokeObjectURL` for HEIC tiles,
  `clearMediaSource`, `thumbnailGrid.innerHTML=''` and DOM tile construction.
- `app.js:renderGridView` / `app.js:showMedia` / `app.js:applyTransform` /
  `app.js:applyFitSize` — creates and mutates grid / viewer DOM:
  `document.createElement`, `appendChild` / `insertBefore` / `replaceChildren`,
  `className`, `hidden`, inline `style` (transform / opacity / width / height /
  display), `innerHTML` for SVG icons, `textContent`, `dataset` and
  `setAttribute('aria-*')`.
- `app.js:renderGridView` / `app.js:gridVirtualizer.jumpToStart` / `jumpToEnd` —
  reads and writes `gridViewContainer.scrollTop` / `scrollHeight` and calls
  `scrollIntoView({behavior:'instant'})`, `focus({preventScroll:true})` and `blur()`
  for the return-to-tile flow and virtualizer jumps.
- `app.js:gridMetrics` / `app.js:applyFitSize` / `app.js:handleWheel` /
  `app.js:handleTouchStart` — uses `document.getBoundingClientRect` /
  `clientWidth` / `clientHeight` / `offsetTop` for the grid metrics, fit sizing,
  zoom anchoring and gesture geometry.
- `app.js:renderGridView` / `app.js:setupEventListeners` — network:
  `fetch(file, {method:'HEAD', cache:'no-store'})` on tile activation and media
  poisoning checks; the optimize / convert requests go through
  `requestOptimization` / `runConvert`.
- `app.js:showMedia` / `app.js:scheduleErrorAdvance` / `app.js:startAutoRefreshTimer` /
  `app.js:startSlideshowTimer` / `app.js:setupEventListeners` — timers and animation
  frames: `setTimeout` / `clearTimeout` (watchdog, error advance, idle hide, tile
  highlight), `setInterval` / `clearInterval` (auto refresh),
  `requestAnimationFrame` / `cancelAnimationFrame` (slideshow progress, resize
  coalescing, post-attach thumbnail start).
- `app.js:showMedia` / `app.js:renderGridView` — `AbortController` creation and
  `signal.aborted` checks for each media load and the HEAD probe;
  `URL.createObjectURL(new Blob([...], {type:'video/quicktime'}))` plus
  `URL.revokeObjectURL`.
- `app.js:toggleTvMode` / `app.js:toggleFullscreen` / `app.js:handleFullscreenChange` —
  the fullscreen browser API: `wrapper.requestFullscreen()`,
  `document.exitFullscreen()`, `document.fullscreenElement` and a
  `fullscreenchange` document listener.
- `app.js:setupEventListeners` — global listener registration on `window` /
  `document` / `viewport` / `gridViewContainer` / `#overlay-header` for click,
  mousedown / move / up, touchstart / move / end / cancel, wheel (passive:false),
  keydown, resize, scroll (passive:true), `visibilitychange` and `fullscreenchange`.
- `app.js:renderGridView` / `app.js:handleTouchEnd` / `app.js:startSlideshowTimer` —
  reads `document.activeElement`, `document.hidden`, `document.fullscreenElement`
  and `window.performance.now()` to gate blur / focus restoration, the refresh,
  the slideshow timing and the gesture speed.
- `app.js:showMedia` / `app.js:showMediaError` — media element control:
  `video.play()` / `pause()`, `video.src` / `controls` / `muted`,
  `img.src` / `display` / `transform` assignment, and clearing the img / video
  handlers on teardown.
- `app.js:showUI` / `app.js:hideUI` — `document.body.classList.add/remove('cursor-hidden')`
  for the idle chrome.
- `app.js:showMedia` / `app.js:toggleTvMode` / `app.js:toggleFullscreen` — console
  output: `console.warn` (media sniff failure, grid / heic issues), `console.error`
  (HEIC handling), `console.info` and `console.log` (fullscreen blocked).
- `app.js:renderGridView` / `app.js:enterFullScreenViewer` / `app.js:showMedia` /
  `app.js:toggleImageMode` / `app.js:toggleTvMode` / `app.js:handleFullscreenChange` —
  persists the viewer state by calling `savePreferences()`.
- `app.js:showMedia` / `app.js:enterFullScreenViewer` / `app.js:setupEventListeners` —
  triggers the cross-module loads `loadMediaMetadata`, `updateMediaActions`,
  `updateViewerStarState`, `updateControlStates`, `setInfoPanel`, `renderGridView`,
  `loadGallery`, `navigateToFolder`, `removeCacheEntry`, `stopViewerSession` and
  `stopGridSession`.
- `settings.js:resolveSettings` / `settings.js:urlSettings` — calls the injected
  `readStored(key)` reader (default returns `null`) and `JSON.parse`s the stored
  JSON; it mutates the caller-supplied `warnings` array.
- `settings.js:normalizeConfig` / `settings.js:directoryUrl` / `settings.js:mediaUrl` /
  `settings.js:relativeMediaPath` / `settings.js:listingEntry` /
  `settings.js:thumbnailUrl` — construct `URL` / `URLSearchParams` objects (no
  network, no storage writes).
- `settings.js:module.exports` — assigns `module.exports` under Node, otherwise
  `globalThis.FolderFrameSettings`; the IIFE touches no DOM.
- `resilience.js:request` — performs `fetch(url, {...options, signal})`, consumes
  `response.text()` / `json()` / `blob()`, and uses `AbortController` with
  `setTimeout` / `clearTimeout` and `signal.addEventListener('abort', ...)`.
- `resilience.js:createImagePool` — `console.warn` by default when a HEIC
  decode / thumbnail job exceeds its deadline, calls the injected
  `createURL` / `revokeURL` for blob object URLs, and uses a new
  `AbortController` per job.
- `resilience.js:module.exports` — assigns `module.exports` under Node, otherwise
  `globalThis.FolderFrameResilience`; no DOM / storage / network use outside the
  injected `download` callback.

## Verification

- Run: `<test-command>`
