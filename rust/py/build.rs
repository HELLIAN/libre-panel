//! Libre Panel's version, as pyproject.toml sets it (its [project] table's): the program says
//! the version of the release it belongs to, whichever language a part is in.

use std::path::Path;

fn main() {
    let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../pyproject.toml");
    println!("cargo:rerun-if-changed={}", source.display());
    let text = std::fs::read_to_string(&source).unwrap_or_default();
    let mut table = "";
    let named = text.lines().find_map(|line| {
        let line = line.trim();
        if line.starts_with('[') {
            table = line;
            return None;
        }
        let value = line
            .strip_prefix("version")?
            .trim_start()
            .strip_prefix('=')?;
        let value = value.trim().strip_prefix('"')?.split('"').next()?;
        (table == "[project]").then(|| value.to_string())
    });
    let version = named.unwrap_or_else(|| {
        println!("cargo:warning=no [project] version in {}", source.display());
        env!("CARGO_PKG_VERSION").to_string()
    });
    println!("cargo:rustc-env=LIBRE_PANEL_VERSION={version}");
}
