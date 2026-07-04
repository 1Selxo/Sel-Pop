# Sel-Pop

<img src="src/resources/logo.jpg" alt="Sel-Pop logo" width="128">

Sel-Pop is a desktop OCR lookup tool forked from [weidot4/Weikipop](https://github.com/weidot4/Weikipop) and built on the original Meikipop project line.

It continuously or manually scans a screen region, performs OCR, and shows dictionary lookups in a popup. It also supports adding cards to Anki via AnkiConnect.

<img width="1695" height="941" alt="image" src="https://github.com/user-attachments/assets/a6105d75-5556-4ea0-8eae-0a394fa52e3c" />
<img width="369" height="439" alt="image" src="https://github.com/user-attachments/assets/7cbc8a7e-f1b1-4822-b8c8-cbba22de7740" />

## Features

- Fast screen-region OCR with multiple OCR backends
- Embedded Yomitan preprocessing and deinflection for every Yomitan-supported language
- Multi-dictionary import support (`.zip` Yomitan and `.pkl`) with language profiles
- Memory-mapped HoshiDicts backend for accelerated Yomitan dictionary imports and exact queries
- Dictionary enable/disable + priority ordering from settings
- Optional Yomitan API integration
- Local language profiles that allow multiple languages and dictionaries to be enabled together
- AnkiConnect export with configurable field mapping
- Local mining log (`data/mining_log.jsonl`) for SRS workflows
- Global shortcuts and tray-based settings
- Cross-platform support (Windows/Linux/macOS)

## Installation

### Option A: prebuilt binaries

1. Open the latest release on GitHub.
2. Download the package for your OS.
3. Launch the executable.

### Option B: run from source

Prerequisites:

- Python 3.10+
- `pip`

Setup:

1. Clone the repository.
2. Install dependencies:
	- `pip install -r requirements.txt`
3. Run the app:
	- `python -m src.main`

## Configuration

Configuration is stored in `config.ini` at runtime and auto-created when settings are saved.

For an initial baseline, copy `config.example.ini` to `config.ini` and adjust values as needed.

If you use the **Google Lens (remote)** provider, set:

- `SEL_POP_GLENS_API_KEY=<your_api_key>`

The legacy `WEIKIPOP_GLENS_API_KEY` name is still accepted for existing setups.

## Usage

- Start the app.
- Use the configured hotkey over text for an enabled language profile to trigger lookup.
- Right-click the tray icon to:
  - select OCR provider
  - choose scan mode/region
  - open settings
- In Settings → Dictionaries:
  - import dictionaries
  - add and enable language profiles
  - assign each dictionary to its source-language profile
  - reorder dictionary priority
  - enable/disable dictionaries
- Use `Scroll Popup` shortcut (default `Alt+Wheel`) to scroll long lookup popups.

For multilingual OCR, use ScreenAI, Google Lens, or an owocr engine configured
for the profile languages. MeikiOCR remains a Japanese-specific backend.

New Yomitan ZIP imports use the bundled HoshiDicts worker automatically. Existing
pickle dictionaries remain supported and can be re-imported to migrate them.

## Development workflow

- Main application entrypoint: `src/main.py`
- OCR providers: `src/ocr/providers/`
- Dictionary pipeline: `src/dictionary/`
- UI: `src/gui/`

Recommended checks before opening a PR:

- `python -m compileall src`
- Run the app and verify tray + popup flow

## Releases

GitHub Actions builds Windows and Linux packages and publishes them as GitHub
Release assets when a `v*` tag is pushed:

```bash
git tag v1.12.6-selpop.1
git push origin v1.12.6-selpop.1
```

You can also run the **Release** workflow manually from GitHub Actions and enter
the release tag there. Manual runs create the tag at the selected commit if it
does not already exist.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution standards.

## License

This project is licensed under GPL-3.0. See [LICENSE](LICENSE).

The embedded language-processing bundle is generated from Yomitan and remains
licensed under GPL-3.0-or-later by the Yomitan Authors.

The accelerated dictionary worker uses [HoshiDicts](https://github.com/Manhhao/hoshidicts)
under GPL-3.0. Its pinned source is included as a Git submodule; rebuild it with
`powershell -File scripts/build_hoshidicts.ps1`.

## Credits

- [weidot4/Weikipop](https://github.com/weidot4/Weikipop), the upstream fork Sel-Pop is based on
- [rtr46](https://github.com/rtr46) for the original Meikipop project
- [zurcGH](https://github.com/zurcGH) for Meikipop-Anki lineage
- [kqq](https://github.com/user-attachments/assets/8d1ebb4e-daeb-4bae-93cc-9ae4a78751df) for being my first initial tester!
