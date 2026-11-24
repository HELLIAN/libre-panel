//! Whether the session is ending (Windows: shut down, restart, sign out), from the moment
//! Windows asks whether it may end until it ends or another program cancels it. Windows starts
//! no program then, and says so in a box that names it: nothing is started while it is so
//! (PresentMon, and the end of its recording session). The app's window for the session's end
//! (winsession.rs) sets it; one flag for every part that starts a program.

use std::sync::atomic::{AtomicBool, Ordering};

static ENDING: AtomicBool = AtomicBool::new(false);

/// ending: whether the session is ending: start no program.
pub fn ending() -> bool {
    ENDING.load(Ordering::Relaxed)
}

/// set_ending: the session begins to end (true), or its end was cancelled (false).
pub fn set_ending(on: bool) {
    ENDING.store(on, Ordering::Relaxed);
}
