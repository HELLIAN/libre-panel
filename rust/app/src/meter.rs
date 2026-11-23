//! How the main loop is doing (meter.py): frame rate, drawing time, data rate, the video link,
//! faults. The loop tells the meter of every frame it draws (`drew`) and every step it takes
//! (`tick`); once a second the numbers are made and handed to the run's status (`stats`),
//! where the tray, the app's panel menu and its Control page read them.

use std::collections::VecDeque;

use lp_devices::base::Counters;
use lp_py::{Dict, Value};

/// drawing times kept: what counts is the last few seconds
const FRAMES: usize = 120;
/// how often the numbers are made
const EVERY_S: f64 = 1.0;
/// rates are measured over the samples kept: the last five seconds
const SAMPLES: usize = 6;

/// What the drivers counted in this run: those of the panels opened before (a replug makes
/// the display anew) and of the one there now.
#[derive(Clone, Copy, Debug, Default)]
pub struct Counted {
    before: Counters,
    now: Counters,
    /// the video's bit rate cap in force, Mbit/s
    pub bit_rate: f64,
}

impl Counted {
    /// The panel's own counters as they stand.
    pub fn follow(&mut self, now: Counters, bit_rate: f64) {
        self.now = now;
        if bit_rate > 0.0 {
            self.bit_rate = bit_rate;
        }
    }

    /// The panel let go: what it counted stays in the run's.
    pub fn closed(&mut self) {
        self.before = self.total();
        self.now = Counters::default();
    }

    pub fn total(&self) -> Counters {
        let (a, b) = (&self.before, &self.now);
        Counters {
            bytes: a.bytes + b.bytes,
            usb_errors: a.usb_errors + b.usb_errors,
            blocks: a.blocks + b.blocks,
            waits: a.waits + b.waits,
            drops: a.drops + b.drops,
            max_depth: a.max_depth.max(b.max_depth),
            slow_blocks: a.slow_blocks + b.slow_blocks,
            panel_restarts: a.panel_restarts + b.panel_restarts,
        }
    }
}

/// Counts frames and times their drawing; makes the stats once a second.
pub struct Meter {
    began: f64,
    next_at: f64,
    drawn: u64,
    times: VecDeque<f64>,
    /// (time, frames drawn, bytes sent, blocks sent)
    samples: VecDeque<(f64, u64, u64, u64)>,
}

/// Python's round(x, 1).
fn round1(x: f64) -> f64 {
    lp_py::round_digits(x, 1)
}

/// statistics.median
fn median(times: &VecDeque<f64>) -> f64 {
    let mut sorted: Vec<f64> = times.iter().copied().collect();
    sorted.sort_by(f64::total_cmp);
    let n = sorted.len();
    match n % 2 {
        1 => sorted[n / 2],
        _ => (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0,
    }
}

impl Meter {
    /// A meter from `now`, on the main loop's clock.
    pub fn new(now: f64, counted: &Counted) -> Meter {
        let mut meter = Meter {
            began: now,
            next_at: now + EVERY_S,
            drawn: 0,
            times: VecDeque::with_capacity(FRAMES),
            samples: VecDeque::with_capacity(SAMPLES),
        };
        let sample = meter.sample(now, counted);
        meter.samples.push_back(sample);
        meter
    }

    fn sample(&self, now: f64, counted: &Counted) -> (f64, u64, u64, u64) {
        let total = counted.total();
        (now, self.drawn, total.bytes, total.blocks)
    }

    /// A frame is drawn (render, fit, transition and message); it took `seconds`.
    pub fn drew(&mut self, seconds: f64) {
        if self.times.len() == FRAMES {
            self.times.pop_front();
        }
        self.times.push_back(seconds);
        self.drawn += 1;
    }

    /// After a step: the new numbers once a second has passed since the last ones (None
    /// before). `target_fps` is the rate the loop plans for, `streaming` whether the panel
    /// wants every frame (video).
    pub fn tick(
        &mut self,
        now: f64,
        target_fps: i64,
        streaming: bool,
        counted: &Counted,
    ) -> Option<Dict> {
        if now < self.next_at {
            return None;
        }
        self.next_at = now + EVERY_S; // (not += EVERY_S: a stalled loop makes no burst)
        if self.samples.len() == SAMPLES {
            self.samples.pop_front();
        }
        let sample = self.sample(now, counted);
        self.samples.push_back(sample);
        Some(self.stats(now, target_fps, streaming, counted))
    }

    /// The numbers. None is "not known" (nothing counted it): the data rate for a driver that
    /// does not count, the video link outside video mode.
    fn stats(&self, now: f64, target_fps: i64, streaming: bool, counted: &Counted) -> Dict {
        let (first, last) = (self.samples[0], self.samples[self.samples.len() - 1]);
        let span = last.0 - first.0;
        let rate = |a: u64, b: u64, scale: f64| match span > 0.0 {
            true => Value::Float(round1((b as f64 - a as f64) / span / scale)),
            false => Value::None,
        };
        let total = counted.total();
        let video = total.blocks > 0; // (the video link has been used in this run)
        let ms = |seconds: f64| Value::Float(round1(seconds * 1000.0));
        let times = !self.times.is_empty();
        let peak = self.times.iter().copied().fold(f64::MIN, f64::max);
        let int = |n: u64| Value::int(n as i64);
        let video_only = |value: Value| match video {
            true => value,
            false => Value::None,
        };
        let pairs = vec![
            ("fps", rate(first.1, last.1, 1.0)),
            ("target_fps", Value::int(target_fps)),
            ("streaming", Value::Bool(streaming)),
            (
                "render_ms",
                match times {
                    true => ms(median(&self.times)),
                    false => Value::None,
                },
            ),
            (
                "render_peak_ms",
                match times {
                    true => ms(peak),
                    false => Value::None,
                },
            ),
            (
                "kbps",
                match total.bytes > 0 {
                    true => rate(first.2, last.2, 1000.0),
                    false => Value::None,
                },
            ),
            ("blocks_per_s", video_only(rate(first.3, last.3, 1.0))),
            ("max_depth", video_only(int(total.max_depth))),
            ("bit_rate", video_only(Value::Float(counted.bit_rate))),
            // (the encoder and what it is fed: Libre Panel's engine, the frames' changed parts)
            ("encoder", video_only(Value::Str("native, boxes".into()))),
            ("waits", video_only(int(total.waits))),
            ("drops", video_only(int(total.drops))),
            ("usb_errors", int(total.usb_errors)),
            ("panel_restarts", int(total.panel_restarts)),
            ("ffmpeg_restarts", int(0)),
            (
                "uptime_s",
                Value::int((now - self.began).round_ties_even() as i64),
            ),
        ];
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn number(stats: &Dict, key: &str) -> Option<f64> {
        match stats.get(key) {
            Some(Value::Float(f)) => Some(*f),
            Some(Value::Int(n)) => lp_py::num::int_to_float(n).ok(),
            _ => None,
        }
    }

    #[test]
    fn the_meter_measures_rates_and_drawing_times_on_its_own_clock() {
        let mut counted = Counted::default();
        let mut meter = Meter::new(100.0, &counted);
        assert!(meter.tick(100.5, 25, false, &counted).is_none()); // (not before its second)
        for n in 1..=25 {
            meter.drew(if n == 7 { 0.090 } else { 0.040 });
        }
        counted.follow(
            Counters {
                bytes: 250_000,
                ..Counters::default()
            },
            0.0,
        );
        let stats = meter
            .tick(101.0, 25, false, &counted)
            .expect("a second has passed");
        assert_eq!(number(&stats, "fps"), Some(25.0));
        assert_eq!(number(&stats, "render_ms"), Some(40.0)); // (the median)
        assert_eq!(number(&stats, "render_peak_ms"), Some(90.0));
        assert_eq!(number(&stats, "kbps"), Some(250.0));
        assert_eq!(
            (number(&stats, "target_fps"), number(&stats, "uptime_s")),
            (Some(25.0), Some(1.0))
        );
        // what nothing counted is unknown: no video link outside video mode
        for key in [
            "blocks_per_s",
            "max_depth",
            "bit_rate",
            "encoder",
            "waits",
            "drops",
        ] {
            assert_eq!(stats.get(key), Some(&Value::None), "{key}");
        }
        let quiet = Meter::new(0.0, &Counted::default()).tick(1.0, 25, false, &Counted::default());
        let quiet = quiet.unwrap();
        assert_eq!(
            (quiet.get("kbps"), quiet.get("render_ms")),
            (Some(&Value::None), Some(&Value::None))
        );
    }

    #[test]
    fn the_meter_reads_the_video_link_and_the_faults_from_the_counters() {
        let mut counted = Counted::default();
        let mut meter = Meter::new(0.0, &counted);
        let said = Counters {
            bytes: 1000,
            blocks: 50,
            waits: 3,
            drops: 1,
            max_depth: 4,
            usb_errors: 1,
            panel_restarts: 1,
            ..Counters::default()
        }; // fmt: skip
        counted.follow(said, 8.0);
        // a replug makes the display anew: what it counted stays in the run's
        counted.closed();
        counted.follow(
            Counters {
                blocks: 25,
                usb_errors: 1,
                max_depth: 2,
                ..Counters::default()
            },
            0.0,
        );
        let stats = meter.tick(1.0, 25, true, &counted).unwrap();
        assert_eq!(number(&stats, "blocks_per_s"), Some(75.0));
        assert_eq!(number(&stats, "max_depth"), Some(4.0));
        assert_eq!(number(&stats, "bit_rate"), Some(8.0)); // (a cap of 0 keeps the one in force)
        assert_eq!(
            (number(&stats, "waits"), number(&stats, "drops")),
            (Some(3.0), Some(1.0))
        );
        assert_eq!(number(&stats, "usb_errors"), Some(2.0));
        assert_eq!(number(&stats, "panel_restarts"), Some(1.0));
        assert_eq!(stats.get("streaming"), Some(&Value::Bool(true)));
    }

    #[test]
    fn the_meter_keeps_only_the_last_frames_and_seconds_and_a_stall_makes_no_burst() {
        let counted = Counted::default();
        let mut meter = Meter::new(0.0, &counted);
        for second in 1..=20 {
            let fps = if second <= 4 { 50 } else { 10 }; // (fast at first, then slow)
            for _ in 0..fps {
                meter.drew(if second <= 4 { 0.5 } else { 0.010 });
            }
            meter.tick(f64::from(second), 25, false, &counted).unwrap();
        }
        assert_eq!(meter.times.len(), FRAMES);
        let stats = meter.tick(21.0, 25, false, &counted).unwrap();
        // (the rate of the last five seconds; the drawing times of the last 120 frames)
        assert_eq!(number(&stats, "fps"), Some(8.0)); // (40 frames in 5 s: none in the last)
        assert_eq!(number(&stats, "render_ms"), Some(10.0));
        assert_eq!(number(&stats, "render_peak_ms"), Some(10.0));
        // a loop that stalled for a minute: one set of numbers, the next a second later
        assert!(meter.tick(81.0, 25, false, &counted).is_some());
        assert!(meter.tick(81.5, 25, false, &counted).is_none());
        assert!(meter.tick(82.0, 25, false, &counted).is_some());
    }
}
