# Contributing

Thanks for helping! The most valuable contributions right now:

1. **Testing hardware.** Own a panel? Run `libre-panel doctor` and attach the
   report to a "panel support" issue — every passing report turns a model
   from *unverified* into *supported*.
2. **Themes.** Build one in the editor and share it (with a license for any
   fonts/images you include): *Export…* on its card in *Start → Themes* gives the zip,
   and [community/themes/README.md](community/themes/README.md) says how to add it to the
   community themes the app lists. After changing a built-in theme,
   `python tools/readme_images.py` renders the pictures in `docs/images`
   again.
3. **Drivers** for the families marked *planned* in [docs/HARDWARE.md](docs/HARDWARE.md).

## Development

Libre Panel is written in Rust (`rust/`, [docs/RUST.md](docs/RUST.md)); Python is its plugin
host and the tests beside the crates' own.

```bash
git clone https://github.com/syntensa/libre-panel
cd libre-panel
# Linux: sudo apt-get install libgtk-3-dev libwebkit2gtk-4.1-dev (the window), as CI does
cargo build --release --manifest-path rust/Cargo.toml
cargo test --release --manifest-path rust/Cargo.toml
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                                           # (with lp-check from rust/target/release)
ruff check . && ruff format --check .
rust/target/release/libre-panel editor
```

Please keep pull requests focused, add tests for behaviour changes, and run the
checks above. By contributing you agree that your work is licensed under
GPL-3.0-or-later.

## Hardware safety

Drivers only use display commands (frames, brightness, sync). Never add
firmware, restart or storage commands without a documented, reversible reason
and a hardware test. When in doubt, ask in an issue first.

## Where things live

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
