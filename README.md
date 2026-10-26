# Herdr QuickView

Preview file paths and HTTP(S) links already visible in a Herdr terminal. Select them **where they appear**, then open macOS Quick Look or an in-place Herdr text/image preview. No Git repository, project scan, project file list, or new tab is required.

[简体中文](README.zh-CN.md)

## See it in action

**Select the path right where it appears.** Open QuickView, then click a highlighted file or link, or type its numbered hint.

![QuickView highlights file paths and a URL in sample terminal output, with numbered hints for instant selection](docs/screenshots/select-paths.png)

**Choose how to preview.** Use **Ctrl+P** to switch between Herdr preview and macOS Quick Look. Your choice is remembered.

| Stay inside Herdr | Use native macOS Quick Look |
| --- | --- |
| ![Herdr code preview with line numbers, syntax highlighting, and the Open in app Ctrl+O button](docs/screenshots/herdr-preview.png) | ![Actual macOS Quick Look panel previewing the same Python file](docs/screenshots/macos-quicklook.png) |
| Read code, search with **Ctrl+F**, or explicitly **Open in app / Ctrl+O**. | Preview supported documents using the system panel; **Esc** returns to path selection, ready for the next file. |

These are actual screenshots of Herdr running in Kitty and the native macOS Quick Look panel, using sample files. The Herdr screenshot shows the earlier toolbar; current previews use a filename title and compact controls. Terminal colors, transparency and system previews depend on your setup.


## Requirements and status

- Herdr **0.9.5+**, Python **3.11+**, macOS or Linux.
- Version **0.1.0**, initial beta version. Local macOS testing is complete for the cases described below; Linux desktop and physical-client shortcut acceptance are still pending.
- Installation needs network access for Python dependencies. macOS Quick Look needs Xcode Command Line Tools to build its small native host; without build tools, Herdr text preview remains available.

## Install

```sh
herdr plugin install fsi044689/herdr-quickview
```

Then configure an entry shortcut as shown under [Open QuickView](#open-quickview). Installation does not add a shortcut automatically.

### From a source checkout

```sh
sh bootstrap.sh
herdr plugin link /absolute/path/to/herdr-quickview
```

The installer looks for a compatible Python before creating the plugin's own `.venv`. You can select one explicitly:

```sh
PYTHON=python3.11 sh bootstrap.sh
```

Re-running the installer repairs an outdated or broken plugin virtual environment. Settings live outside the checkout and are preserved. If QuickView does not appear, re-run the installer and inspect its output; the action log is available through `herdr plugin log list --plugin sylar.herdr-quickview`.

## Open QuickView

The plugin does **not** install a keyboard binding automatically. From the terminal pane you want to inspect, invoke its action:

```sh
herdr plugin action invoke sylar.herdr-quickview.quickview
```

For a shortcut, add this to your Herdr configuration, using an available key:

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "sylar.herdr-quickview.quickview"
description = "Herdr QuickView"
```

Validate and reload your Herdr configuration after editing it. `prefix` means your configured Herdr prefix, which is not necessarily Ctrl+B.


## Use it

1. Bring the desired file path or HTTP(S) URL into view, then open QuickView.
2. Recognized targets receive a colored highlight and a numbered or lettered badge. **Click the original text/badge, or type its label** to preview immediately. Tab / Shift+Tab and Enter also work.
3. On the path-selection screen, **Ctrl+P** or the clickable mode label switches between macOS Quick Look and the Herdr popup. The choice is saved and applies when you select a target. Preview-mode selection is available only from this screen. System Quick Look is available only on local macOS with the native helper installed.
4. In Herdr text preview, **Ctrl+F** searches. Local static PNG, JPEG and WebP files use the same preview area for images. **Open app / Ctrl+O** hands the file to its system-associated application, or the URL to the default browser. **Copy** copies the complete address. Unsupported formats also offer these buttons; apps open only on your explicit request.
5. **Ctrl+L** or **中文 / English** changes the plugin language and remembers it. English is the default; no OS-language detection is performed.
6. In Herdr preview, **Esc / q** or **Back** returns to the same path selection so you can preview another file. In macOS Quick Look, close the system panel with Esc, Space, or Cmd+W to return to selection. **Esc / q** from selection exits QuickView; **Ctrl+Q** exits directly from the plugin UI. While searching, the first Esc leaves search. A non-empty query with no match shows a red, bold **Not found** in the status bar; it disappears when the query matches, is cleared, or search ends.

Herdr preview includes a collapsible **file/link sidebar** on the left. Click an item to preview it on the right, or use **Ctrl+J** for next and **Ctrl+K** for previous. **Ctrl+T** shows/hides the list; **Tab / Shift+Tab** moves between the list and preview. In the list, **↑ / ↓** previews the next or previous item directly and stops at either end; **Enter** also opens the selection. The mouse wheel moves the list selection and focuses the list, ready for Enter. The list starts folded for a single target or an initial window narrower than 72 columns; Ctrl+T can still expand it.

The sidebar reuses this snapshot's visible targets, deduplicated by file/line or URL. It does not scan a project or preload files. Rapid preview changes wait 120 ms before reading only the final selection. Switching inside Herdr preview stays in Herdr, including after Quick Look fails; use Back to choose another target in the snapshot with the saved macOS mode. Switching does not undo an already-issued Open in app or copy request; it stops feedback from the old preview.

While switching within Herdr preview, the previous title, address, code colors and image stay together during the 120ms debounce. A fast read replaces them together; a read still pending after another 120ms switches to the new address and a loading page. Returning to the displayed file before it changes preserves the reading position. **Copy** uses the address currently displayed; **Open app** waits until loading finishes.

The preview title shows the current filename and referenced line, or a URL's host and resource path. Long titles preserve both ends; the complete address remains below. Compact toolbar buttons fit windows as narrow as 60 columns. The sidebar emphasizes file names and URL sites above their softer directory/resource lines; current and keyboard-selected items keep their distinct highlights. Long entries use a middle ellipsis so both ends remain visible. The preview address wraps to at most three rows; click it and use **Home / End** (Fn+← / Fn+→ on Mac) to see its beginning/end; Ctrl+A / Ctrl+E also work when your Herdr prefix does not intercept them. **Copy** always copies the complete value. Wheel-only selection shows a separate **Selected:** address in the navigation bar when space permits; press Enter to preview that item.

The **Copy** button copies the real complete path or original URL, without terminal display wraps. Number labels use 1–9, 0, then letters; `q` is reserved for closing. With many targets, labels become equal-length multiple-character codes; finish the entire code to select a target, or use Backspace to correct it.

The selection footer shortens long addresses while preserving the filename and line suffix, and puts partially typed labels first. Tab / Shift+Tab clears feedback from the previous action when selecting the next target; snapshot warnings remain.

Code highlighting uses your terminal's ANSI palette and preserves its background. Opening a local file with a line suffix, such as `src/example.py:150`, initially places that line near the center where space permits, accounting for wrapped lines. The referenced line stays bold and underlined when you move the cursor or search; scrolling remains free. Files without a line suffix start at the top without a target marker. This applies to Herdr text preview; macOS Quick Look keeps its system behavior.

## Preview support

| Content | macOS Quick Look | Herdr popup |
| --- | --- | --- |
| UTF-8 text, source code, JSON | System preview, subject to installed extensions | Text, line numbers, syntax highlighting |
| Markdown, local HTML, SVG | System-rendered content where supported | Source text |
| Local static PNG, JPEG, WebP | System preview where supported | Image; requires a compatible terminal |
| Animated images, PDF, ZIP, other binary data | Depends on the system and its Quick Look extensions | Explicit unsupported-format message, with Open in app available |
| HTTP(S) pages | Verified locally on macOS 26.2; not guaranteed on other versions | Extracted title, readable text and links; also plain text and JSON |

Text and web preview read at most 2 MiB. Web preview has an 8-second UI deadline; it does not run JavaScript, share browser login state, or render images/PDFs. A ready system panel confirms visibility and focus, **not** successful page loading. Apple's Quick Look item API documents file URLs, so online URL preview is treated as version-dependent behavior.

Images use [Herdr's supported Kitty graphics protocol](https://herdr.dev/docs/configuration/#kitty-graphics), without running Kitty or requiring `icat`. The decoded PNG is sent directly, without conversion to colored text characters or requiring terminal cell pixel dimensions. A compatible terminal is required; if the image area stays blank, use **Ctrl+O** to open the file in its default application. The user confirmed clear image display in the production popup on local macOS with Kitty and Herdr 0.9.5. Other terminals and Linux desktop rendering remain unverified. PNG/JPEG/WebP decoding uses Pillow, installed into the plugin's own environment.

Only the selected local static image is decoded, in a cancellable worker with an 8-second deadline. Files are limited to 32 MiB; PNG/WebP to 16 million pixels. JPEG permits originals up to 100 million pixels, using reduced decoding and a 16-million-pixel decoded limit. Previews have a maximum edge of 1200 pixels; orientation and transparency are handled before display. Switching targets stops the old worker and removes QuickView's own image; returning or exiting also removes it. Image URLs are not automatically downloaded. PDFs, Office documents, archives, audio and video are outside the in-terminal image feature.

## Recognition and interaction limits

- QuickView captures a static snapshot of the visible tab. It marks files from the originating pane only; the underlying CLI keeps running. Esc from path selection returns to live output. Returning from a preview keeps the same snapshot and numbered hints; it does not rescan new output. QuickView does not scroll the terminal or read history.
- Paths must resolve to existing local files, relative to the originating pane's working directory. Quoted paths, spaces, Unicode, Markdown targets, shell-escaped spaces, local `file://` URIs, and line suffixes such as `:12:3` or `#L12` are supported.
- Terminal soft wraps are restored using Herdr selection data. CLI-inserted hard wraps are joined only when their structure and an existing file support that interpretation. Indented `file://` continuations may split a percent escape. Ambiguous paths are left unmarked; hard-wrapped `#L` anchors can lose the line number.
- URLs are recognized without network requests. Only explicit HTTP(S) URLs without embedded credentials are accepted. Hard wraps require a complete explicit wrapper and indented continuation. Use spaces, angle brackets or Markdown to separate URLs from adjoining prose.
- Window resizing invalidates old hit coordinates; close and reopen QuickView. Popup borders/gutters may crop edge targets. Terminal images and pane borders are not reproduced. The footer avoids clickable targets and may be absent if no suitable row is available; long messages/addresses are limited by terminal width.
- SSH environments use Herdr preview and do not launch remote GUI apps. Remote attachment without SSH environment markers is not reliably detectable yet.
- File/web contents, paths and external-program errors are never translated. System Quick Look controls follow macOS behavior; switching the plugin language changes its own controls and subsequent messages.

## Settings

```sh
herdr plugin config-dir sylar.herdr-quickview
```

Create or edit `config.toml` in that directory if needed:

```toml
preview_mode = "macos" # or "herdr"; non-macOS defaults to "herdr"
language = "en"        # or "zh"
```

Changes from the UI preserve other fields, comments and configuration symlinks. Invalid settings are reported in the popup and are not overwritten. Correct the file manually if it cannot be parsed. Operations in progress ignore mode/language changes; Esc remains available to cancel QuickView.

## Development and evidence

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

The suite covers path/URL recognition, local HTTP fixtures, native handshake/failure simulation, settings, shell startup checks, real prompt_toolkit input-pipe/mouse sequences, isolated image decoding, cancellation, resource limits, and image cleanup. Native file previews were additionally inspected on macOS 26.2 with synthetic samples; installed Quick Look extensions affect results. Image protocol responses were checked in a real Herdr PTY. The user also accepted the direct-PNG image preview in the production popup on local macOS with Kitty; this does not establish visual correctness on other clients.

A clean Python 3.11 source build and the primary Python 3.14 environment are checked separately. Platform mocks and input-pipe tests do not establish Linux desktop behavior, actual Herdr-client key delivery, or default-app rendering. Those acceptance checks remain explicit release gaps. See [the Chinese notes](README.zh-CN.md) for the detailed prior acceptance matrix.

## License

[MIT License](LICENSE). Copyright (c) 2026 sylar.
