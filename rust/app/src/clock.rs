//! The moment the app's server takes for now: the clock's, or the one the tests name.

use std::sync::OnceLock;

/// the tests' own: the moment (seconds since 1970) the app takes for now in what it tells
/// (the pictures its server draws of "now", the history's spans, the date in a file's name,
/// the time on a report), instead of the clock's: what it tells is then the same whenever
/// it is asked for. What runs by the clock (the panel's frames, a clip, a wait) stays on it
pub const NOW_ENV: &str = "LP_NOW";

/// Seconds since 1970, now.
pub fn now() -> f64 {
    static NAMED: OnceLock<Option<f64>> = OnceLock::new();
    let named = NAMED.get_or_init(|| {
        let named = std::env::var(NOW_ENV).ok()?;
        named.parse().ok().filter(|at: &f64| at.is_finite())
    });
    named.unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |since| since.as_secs_f64())
    })
}
