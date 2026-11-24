//! The time the panels' loops and what they draw go by: the computer's, or a clip's.
//!
//! A clip (`libre-panel clip`, tools/media_panel.py) runs the loops frame by frame on a clock
//! of its own, so that a clip is the same on every run whatever the machine: before each frame
//! it says how far into the clip the frame is and what the time of day is ([`set_clip`]), and
//! from then on every part that goes by the time takes these. Without a clip each is the
//! computer's own, as it always was (one atomic flag read more).

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::OnceLock;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

static CLIP: AtomicBool = AtomicBool::new(false);
/// seconds into the clip (bits of an f64)
static SECONDS: AtomicU64 = AtomicU64::new(0);
/// the time of day, seconds since 1970 (bits of an f64)
static WALL: AtomicU64 = AtomicU64::new(0);

/// The moment the clip's seconds count from, as an [`Instant`].
fn base() -> Instant {
    static BASE: OnceLock<Instant> = OnceLock::new();
    *BASE.get_or_init(Instant::now)
}

/// From now on the time is a clip's: `seconds` into it (not below 0), the time of day `wall`.
pub fn set_clip(seconds: f64, wall: f64) {
    base();
    SECONDS.store(seconds.max(0.0).to_bits(), Ordering::Relaxed);
    WALL.store(wall.to_bits(), Ordering::Relaxed);
    CLIP.store(true, Ordering::Release);
}

/// Whether the time is a clip's.
pub fn in_clip() -> bool {
    CLIP.load(Ordering::Acquire)
}

fn clip_seconds() -> f64 {
    f64::from_bits(SECONDS.load(Ordering::Relaxed))
}

/// `Instant::now()`, or the clip's moment.
pub fn now() -> Instant {
    match in_clip() {
        true => base() + Duration::from_secs_f64(clip_seconds()),
        false => Instant::now(),
    }
}

/// The time of day in seconds since 1970: the computer's, or the clip's.
pub fn wall() -> f64 {
    match in_clip() {
        true => f64::from_bits(WALL.load(Ordering::Relaxed)),
        false => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0.0, |since| since.as_secs_f64()),
    }
}

/// time.monotonic(): seconds from the first time it is read; in a clip, the clip's seconds.
pub fn monotonic() -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    match in_clip() {
        true => clip_seconds(),
        false => START.get_or_init(Instant::now).elapsed().as_secs_f64(),
    }
}
