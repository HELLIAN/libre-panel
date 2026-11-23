//! What outlives a panel's loop: the services' host and the services that run. The loop of
//! `libre-panel run` has its own; the background app's is the app's, so that the services go
//! on while the panel is paused (service.py's BackgroundApp: its PluginHost and
//! ServiceManager).

use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use lp_config::Config;
use lp_services::host::PluginHost;
use lp_services::manager::Manager;

fn say(message: &str) {
    crate::logs::say("libre_panel.services", message);
}

/// What else follows config.toml (the background app's keyboard shortcuts).
pub type Also = Box<dyn Fn(&Config) + Send>;

pub struct Company {
    /// when the host's clock began: the loops count from it too
    pub began: Instant,
    pub host: Arc<PluginHost>,
    manager: Mutex<Manager>,
    /// the services config.toml enables that this build lacks, as they were last said
    lacking: Mutex<Vec<String>>,
    also: Mutex<Option<Also>>,
}

/// Seconds since `began` on the clock the loops go by (a clip's where one runs them).
pub fn since(began: Instant) -> f64 {
    lp_render::clock::now()
        .saturating_duration_since(began)
        .as_secs_f64()
}

/// When this process's clock began: as its first company was made, and every company after it
/// in the process counts from there too (the plugin host, started once, is told it:
/// [`clock_zero`]).
fn began() -> Instant {
    static BEGAN: OnceLock<Instant> = OnceLock::new();
    *BEGAN.get_or_init(lp_render::clock::now)
}

/// Where this process's clock began on the computer's own, the clock Python's
/// `time.perf_counter` reads (CLOCK_MONOTONIC on Linux and the BSDs, CLOCK_UPTIME_RAW on
/// macOS, QueryPerformanceCounter on Windows), in its seconds: the plugin host counts
/// `libre_panel.clock()` from there, so that a plugin's own times compare with the `now` it is
/// drawn with and with the time stamps of states. None in a clip, whose clock is its own.
pub fn clock_zero() -> Option<f64> {
    if lp_render::clock::in_clip() {
        return None;
    }
    let began = began();
    // (the two read one after the other: a few hundred nanoseconds apart)
    let computer = perf_counter()?;
    Some(computer - since(began))
}

/// The computer's monotonic clock in seconds, as Python's `time.perf_counter` reads it.
fn perf_counter() -> Option<f64> {
    #[cfg(windows)]
    {
        use windows_sys::Win32::System::Performance::{
            QueryPerformanceCounter, QueryPerformanceFrequency,
        };
        let (mut ticks, mut rate) = (0i64, 0i64);
        // SAFETY: two calls that each write one number where they are told.
        let read = unsafe {
            QueryPerformanceCounter(&mut ticks) != 0 && QueryPerformanceFrequency(&mut rate) != 0
        };
        (read && rate > 0).then(|| ticks as f64 / rate as f64)
    }
    #[cfg(unix)]
    {
        #[cfg(target_os = "macos")]
        let which = libc::CLOCK_UPTIME_RAW;
        #[cfg(not(target_os = "macos"))]
        let which = libc::CLOCK_MONOTONIC;
        let mut time = libc::timespec {
            tv_sec: 0,
            tv_nsec: 0,
        };
        // SAFETY: the call writes the time where it is told.
        let read = unsafe { libc::clock_gettime(which, &mut time) } == 0;
        read.then_some(time.tv_sec as f64 + time.tv_nsec as f64 * 1e-9)
    }
    #[cfg(not(any(windows, unix)))]
    {
        None
    }
}

/// `<settings>/plugins-data`: where each service keeps its files (power's totals, the memory's
/// database)
pub const SERVICES_DATA: &str = "plugins-data";

impl Company {
    /// The services' host and manager, with the folder of their files under `settings` known
    /// before any of them starts: a service reads what it kept as it starts (the power count
    /// its totals), and one that makes its file then (the memory) would fail without it.
    pub fn new(settings: &std::path::Path) -> Arc<Company> {
        // (the clock the services and the loops count by: a clip's where one runs them)
        let began = began();
        let host = PluginHost::new(Box::new(move || since(began)));
        host.set_data_dir(settings.join(SERVICES_DATA));
        let manager = Manager::new(&host, crate::services::parts());
        Arc::new(Company {
            began,
            host,
            manager: Mutex::new(manager),
            lacking: Mutex::new(Vec::new()),
            also: Mutex::new(None),
        })
    }

    /// What follows config.toml beside the services.
    pub fn set_also(&self, also: Option<Also>) {
        if let Ok(mut held) = self.also.lock() {
            *held = also;
        }
    }

    /// The services config.toml enables, started, stopped or started anew as it says; those
    /// this build does not have yet are said once.
    pub fn follow(&self, config: &Config) {
        let parts = crate::services::parts();
        // (the plugins' services, run in the plugin host where plugins are installed)
        let settings = config
            .path
            .as_deref()
            .and_then(std::path::Path::parent)
            .map_or_else(lp_config::dirs::config_dir, std::path::Path::to_path_buf);
        let plugins = match crate::plugins::shared(&settings) {
            Some(Ok(host)) => host.names("services"),
            _ => Vec::new(),
        };
        if !plugins.is_empty() {
            self.plugin_services(&settings, config);
        }
        let mut enabled: Vec<String> = Vec::new();
        let mut lacking = Vec::new();
        for name in &config.services.enabled {
            match parts.contains_key(name.as_str()) || plugins.contains(name) {
                true => enabled.push(name.clone()),
                false => lacking.push(name.clone()),
            }
        }
        if parts.contains_key("script") {
            enabled.push("script".into()); // (the tests' own)
        }
        if let Ok(mut said) = self.lacking.lock() {
            if lacking != *said {
                for name in &lacking {
                    // (ServiceManager's words for one nobody brings)
                    say(&format!("service {}: not installed", lp_py::repr(name)));
                }
                *said = lacking;
            }
        }
        if let Ok(mut manager) = self.manager.lock() {
            manager.apply(&enabled, &crate::services::options_of(config));
            for note in manager.notes() {
                say(&note);
            }
        }
        if let Ok(also) = self.also.lock() {
            if let Some(also) = also.as_ref() {
                also(config);
            }
        }
    }

    /// The plugins' services handed to the manager, once.
    fn plugin_services(&self, settings: &std::path::Path, config: &Config) {
        let Ok(mut manager) = self.manager.lock() else {
            return;
        };
        if manager.has_remote() {
            return;
        }
        if let Some(Ok(host)) = crate::plugins::shared(settings) {
            let remote =
                crate::plugins::RemoteServices::new(host, &self.host, config.path.as_deref());
            manager.set_remote(Box::new(remote));
        }
    }

    /// Each service config.toml enables and its state (`running`, `failed: ...`, `not
    /// installed`, `stopped`), as the manager has them.
    pub fn states(&self) -> indexmap::IndexMap<String, String> {
        self.manager
            .lock()
            .map(|m| m.state.clone())
            .unwrap_or_default()
    }

    /// A running service's summary for its card; None where it says nothing.
    pub fn summary(&self, name: &str) -> Option<lp_py::Value> {
        self.manager.lock().ok()?.summary_of(name)
    }

    /// A running service's status for its console page; None where it does not run.
    pub fn status(&self, name: &str) -> Option<lp_py::Value> {
        self.manager.lock().ok()?.status_of(name)
    }

    /// Stops an enabled service and starts it again; its new state.
    pub fn restart(&self, name: &str) -> Result<String, String> {
        let mut manager = self.manager.lock().map_err(|_| "the services are stuck")?;
        let done = manager.restart(name);
        for note in manager.notes() {
            say(&note);
        }
        done
    }

    /// Every service stopped, the last started first.
    pub fn stop_services(&self) {
        if let Ok(mut manager) = self.manager.lock() {
            manager.stop();
            for note in manager.notes() {
                say(&note);
            }
        }
    }

    pub fn close(&self) {
        self.host.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_services_know_their_folder_before_any_of_them_starts() {
        let settings = std::env::temp_dir().join(format!("lp-company-{}", std::process::id()));
        let company = Company::new(&settings);
        let host = lp_services::host::ServiceHost::new(&company.host, "power");
        let folder = host.data_dir();
        let _ = std::fs::remove_dir_all(&settings);
        assert_eq!(folder, Some(settings.join(SERVICES_DATA).join("power")));
    }
}
