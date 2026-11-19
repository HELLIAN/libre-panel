//! Weather from OpenWeatherMap (https://openweathermap.org) with the user's own API key, put
//! into the shape of Open-Meteo's answer: the present weather, the hours, the days and the rain
//! of the next two hours are then read as weather.rs reads Open-Meteo's, the same readings with
//! the same units, labels and words.
//!
//! A key with One Call 3.0 (OpenWeather's "One Call by Call", a number of calls a day free) gets
//! its answer: the present weather, 48 hours, 8 days and the next hour's rain by the minute.
//! Any other key gets what every key has: the present weather and the forecast of 5 days in
//! 3-hour steps. Its hours between the steps are drawn from the steps either side (the
//! temperature on the straight line between them, the rest from the nearer one), a day is shown
//! where the forecast holds all of it (today from now on, with what was seen of today since the
//! app started), and it says nothing of the rain of the next two hours. Only the place's
//! coordinates and the key go to OpenWeatherMap; the key is in no message.

use lp_py::datetime::DateTime;
use lp_py::zones::{fromtimestamp, Zone};
use lp_py::{float_repr, Dict, Value};

use crate::weather::{quote_plus, DAYS};

/// OpenWeatherMap's API; the paths of its answers follow (`/3.0/onecall`, `/2.5/weather`, ...).
pub const BASE_URL: &str = "https://api.openweathermap.org/data";

/// OpenWeatherMap's condition (https://openweathermap.org/weather-conditions) as the WMO's code
/// Open-Meteo gives, which the panel's symbols and words are of.
pub fn wmo_code(id: i64) -> i64 {
    match id {
        200..=232 => 95,             // thunderstorm
        300 | 310 => 51,             // light drizzle
        301 | 311 | 313 | 321 => 53, // drizzle
        302 | 312 | 314 => 55,       // heavy drizzle
        500 => 61,                   // light rain
        501 => 63,                   // moderate rain
        502..=504 => 65,             // heavy rain
        511 => 66,                   // freezing rain
        520 => 80,                   // light shower rain
        521 | 531 => 81,             // shower rain, ragged shower rain
        522 => 82,                   // heavy shower rain
        600 => 71,                   // light snow
        601 => 73,                   // snow
        602 => 75,                   // heavy snow
        611..=616 => 67,             // sleet, rain and snow
        620 | 621 => 85,             // shower snow
        622 => 86,                   // heavy shower snow
        771 => 82,                   // squalls
        781 => 95,                   // tornado
        700..=799 => 45,             // mist, smoke, haze, dust, fog, sand, ash
        800 => 0,                    // clear sky
        801 => 1,                    // few clouds
        802 => 2,                    // scattered clouds
        803 | 804 => 3,              // broken and overcast clouds
        _ => 3,
    }
}

/// `data[path[0]][path[1]]...` as a number.
fn number(data: &Dict, path: &[&str]) -> Option<f64> {
    let (last, before) = path.split_last()?;
    let mut table = data;
    for key in before {
        table = table.get(*key)?.as_dict()?;
    }
    table.get(*last)?.to_f64().filter(|value| value.is_finite())
}

/// The first of a thing's `weather` conditions: its id and whether its icon is the day's.
fn condition(data: &Dict) -> (Option<i64>, Option<bool>) {
    let first = data
        .get("weather")
        .and_then(Value::as_list)
        .and_then(|list| list.first())
        .and_then(Value::as_dict);
    let Some(first) = first else {
        return (None, None);
    };
    let id = first.get("id").and_then(Value::to_f64).map(|id| id as i64);
    let day =
        first
            .get("icon")
            .and_then(Value::as_str)
            .and_then(|icon| match icon.chars().last() {
                Some('d') => Some(true),
                Some('n') => Some(false),
                _ => None,
            });
    (id, day)
}

/// A temperature or a speed as Open-Meteo gives it: to a tenth (and never "-0.0").
fn tenth(value: f64) -> Value {
    let value = (value * 10.0).round() / 10.0;
    Value::Float(if value == 0.0 { 0.0 } else { value })
}

/// A probability of 0 to 1 as Open-Meteo's per cent.
fn per_cent(pop: f64) -> Value {
    Value::int((pop.clamp(0.0, 1.0) * 100.0).round() as i64)
}

/// The local time `t` (Unix seconds) at `offset` seconds from UTC, without its zone.
fn local(t: f64, offset: i64) -> Option<DateTime> {
    fromtimestamp(&Zone::utc(), t + offset as f64).map(|(at, _)| at)
}

fn minute_text(at: &DateTime) -> String {
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}",
        at.year, at.month, at.day, at.hour, at.minute
    )
}

fn day_text(at: &DateTime) -> String {
    format!("{:04}-{:02}-{:02}", at.year, at.month, at.day)
}

fn table(pairs: Vec<(&str, Value)>) -> Value {
    Value::Dict(
        pairs
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    )
}

/// What the units are called, as Open-Meteo calls them: the temperature's and the wind's.
fn units(imperial: bool) -> (&'static str, &'static str) {
    if imperial {
        ("°F", "mp/h")
    } else {
        ("°C", "km/h")
    }
}

/// The wind as Open-Meteo gives it: km/h where OpenWeatherMap's metric answer has m/s.
fn wind(speed: f64, imperial: bool) -> Value {
    tenth(if imperial { speed } else { speed * 3.6 })
}

/// The present weather: Open-Meteo's `current` and `current_units`.
fn current(
    temperature: Option<f64>,
    feels: Option<f64>,
    humidity: Option<f64>,
    speed: Option<f64>,
    (id, day): (Option<i64>, Option<bool>),
    imperial: bool,
) -> (Value, Value) {
    let (temp, wind_unit) = units(imperial);
    let mut now = Vec::new();
    if let Some(value) = temperature {
        now.push(("temperature_2m", tenth(value)));
    }
    if let Some(value) = feels {
        now.push(("apparent_temperature", tenth(value)));
    }
    if let Some(value) = humidity {
        now.push(("relative_humidity_2m", Value::int(value.round() as i64)));
    }
    if let Some(value) = speed {
        now.push(("wind_speed_10m", wind(value, imperial)));
    }
    if let Some(id) = id {
        now.push(("weather_code", Value::int(wmo_code(id))));
    }
    if let Some(day) = day {
        now.push(("is_day", Value::int(i64::from(day))));
    }
    let named = table(vec![
        ("temperature_2m", Value::Str(temp.into())),
        ("apparent_temperature", Value::Str(temp.into())),
        ("relative_humidity_2m", Value::Str("%".into())),
        ("wind_speed_10m", Value::Str(wind_unit.into())),
        ("weather_code", Value::Str("wmo code".into())),
        ("is_day", Value::Str(String::new())),
    ]);
    (table(now), named)
}

/// One hour of the forecast.
struct Hour {
    at: DateTime,
    temperature: Option<f64>,
    code: Option<i64>,
    rain: Option<f64>,
    day: Option<bool>,
}

fn hourly(hours: &[Hour], imperial: bool) -> (Value, Value) {
    let (temp, _) = units(imperial);
    let column = |value: &dyn Fn(&Hour) -> Value| Value::List(hours.iter().map(value).collect());
    let or_none = |value: Option<Value>| value.unwrap_or(Value::None);
    let rows = table(vec![
        ("time", column(&|hour| Value::Str(minute_text(&hour.at)))),
        (
            "temperature_2m",
            column(&|hour| or_none(hour.temperature.map(tenth))),
        ),
        (
            "weather_code",
            column(&|hour| or_none(hour.code.map(|c| Value::int(wmo_code(c))))),
        ),
        (
            "precipitation_probability",
            column(&|hour| or_none(hour.rain.map(per_cent))),
        ),
        (
            "is_day",
            column(&|hour| or_none(hour.day.map(|d| Value::int(i64::from(d))))),
        ),
    ]);
    let named = table(vec![
        ("temperature_2m", Value::Str(temp.into())),
        ("precipitation_probability", Value::Str("%".into())),
    ]);
    (rows, named)
}

/// One day of the forecast: its highest and lowest temperature, its most severe weather (as
/// Open-Meteo's day has it) and its likeliest precipitation.
struct Day {
    at: DateTime,
    high: f64,
    low: f64,
    code: Option<i64>,
    rain: Option<f64>,
}

fn daily(days: &[Day], imperial: bool) -> (Value, Value) {
    let (temp, _) = units(imperial);
    let column = |value: &dyn Fn(&Day) -> Value| Value::List(days.iter().map(value).collect());
    let rows = table(vec![
        ("time", column(&|day| Value::Str(day_text(&day.at)))),
        ("temperature_2m_max", column(&|day| tenth(day.high))),
        ("temperature_2m_min", column(&|day| tenth(day.low))),
        (
            "weather_code",
            column(&|day| day.code.map_or(Value::None, |c| Value::int(wmo_code(c)))),
        ),
        (
            "precipitation_probability_max",
            column(&|day| day.rain.map_or(Value::None, per_cent)),
        ),
    ]);
    let named = table(vec![
        ("temperature_2m_max", Value::Str(temp.into())),
        ("temperature_2m_min", Value::Str(temp.into())),
        ("precipitation_probability_max", Value::Str("%".into())),
    ]);
    (rows, named)
}

/// The more severe of two conditions, as Open-Meteo's day takes the most severe of its hours:
/// the higher WMO code.
fn severer(a: Option<i64>, b: Option<i64>) -> Option<i64> {
    match (a, b) {
        (Some(a), Some(b)) => Some(if wmo_code(a) >= wmo_code(b) { a } else { b }),
        (a, b) => a.or(b),
    }
}

fn list<'a>(data: &'a Dict, key: &str) -> &'a [Value] {
    data.get(key).and_then(Value::as_list).unwrap_or(&[])
}

fn offset_of(value: Option<&Value>) -> i64 {
    value
        .and_then(Value::to_f64)
        .filter(|f| f.is_finite())
        .map_or(0, |f| f.trunc() as i64)
}

/// One Call 3.0's answer as Open-Meteo's.
pub fn from_onecall(data: &Dict, imperial: bool, now: f64) -> Dict {
    let offset = offset_of(data.get("timezone_offset"));
    let mut out = Dict::default();
    out.insert("utc_offset_seconds".into(), Value::int(offset));
    if let Some(now_table) = data.get("current").and_then(Value::as_dict) {
        let (values, named) = current(
            number(now_table, &["temp"]),
            number(now_table, &["feels_like"]),
            number(now_table, &["humidity"]),
            number(now_table, &["wind_speed"]),
            condition(now_table),
            imperial,
        );
        out.insert("current".into(), values);
        out.insert("current_units".into(), named);
    }
    let mut hours = Vec::new();
    for hour in list(data, "hourly").iter().filter_map(Value::as_dict) {
        let Some(at) = number(hour, &["dt"]).and_then(|t| local(t, offset)) else {
            continue;
        };
        let (code, day) = condition(hour);
        hours.push(Hour {
            at,
            temperature: number(hour, &["temp"]),
            code,
            rain: number(hour, &["pop"]),
            day,
        });
    }
    if !hours.is_empty() {
        let (rows, named) = hourly(&hours, imperial);
        out.insert("hourly".into(), rows);
        out.insert("hourly_units".into(), named);
    }
    let mut days = Vec::new();
    for day in list(data, "daily").iter().filter_map(Value::as_dict) {
        let at = number(day, &["dt"]).and_then(|t| local(t, offset));
        let high = number(day, &["temp", "max"]);
        let low = number(day, &["temp", "min"]);
        if let (Some(at), Some(high), Some(low)) = (at, high, low) {
            days.push(Day {
                at: at.midnight(),
                high,
                low,
                code: condition(day).0,
                rain: number(day, &["pop"]),
            });
        }
    }
    if !days.is_empty() {
        let (rows, named) = daily(&days, imperial);
        out.insert("daily".into(), rows);
        out.insert("daily_units".into(), named);
    }
    if let Some(rain) = rain_steps(data, offset, now) {
        out.insert("minutely_15".into(), rain);
    }
    out
}

/// A quarter of an hour in seconds, the steps of Open-Meteo's rain.
const QUARTER: f64 = 900.0;

/// The rain of the next two hours in Open-Meteo's 15-minute steps (each step the rain of the 15
/// minutes up to its time, in mm): from One Call's rain by the minute (mm/h) for the hour it
/// holds, and from its hours' rain and snow after it. The step that holds the present moment is
/// taken from the minutes of it still to come.
fn rain_steps(data: &Dict, offset: i64, now: f64) -> Option<Value> {
    let minutes: Vec<(f64, f64)> = list(data, "minutely")
        .iter()
        .filter_map(Value::as_dict)
        .filter_map(|minute| {
            Some((
                number(minute, &["dt"])?,
                number(minute, &["precipitation"])?,
            ))
        })
        .collect();
    if minutes.is_empty() {
        return None;
    }
    let hours: Vec<(f64, f64)> = list(data, "hourly")
        .iter()
        .filter_map(Value::as_dict)
        .filter_map(|hour| {
            let fallen = number(hour, &["rain", "1h"]).unwrap_or(0.0)
                + number(hour, &["snow", "1h"]).unwrap_or(0.0);
            Some((number(hour, &["dt"])?, fallen))
        })
        .collect();
    let first = (now / QUARTER).ceil() * QUARTER;
    let (mut times, mut amounts) = (Vec::new(), Vec::new());
    for step in 0..12 {
        let end = first + QUARTER * f64::from(step);
        let start = end - QUARTER;
        // the minutes of the step that are known: a mean of their rate for the quarter
        let known: Vec<f64> = minutes
            .iter()
            .filter(|(t, _)| *t >= start && *t < end)
            .map(|(_, rate)| *rate)
            .collect();
        let amount = if known.is_empty() {
            match hours
                .iter()
                .find(|(t, _)| *t <= start && start < t + 3600.0)
            {
                Some((_, fallen)) => fallen / 4.0,
                None => break,
            }
        } else {
            known.iter().sum::<f64>() / known.len() as f64 / 4.0
        };
        let Some(at) = local(end, offset) else {
            break;
        };
        times.push(Value::Str(minute_text(&at)));
        amounts.push(Value::Float((amount * 100.0).round() / 100.0));
    }
    (!times.is_empty()).then(|| {
        table(vec![
            ("time", Value::List(times)),
            ("precipitation", Value::List(amounts)),
        ])
    })
}

/// What was seen of today in the present weather since the app started: its date (as an
/// ordinal), lowest and highest temperature and most severe condition. The free forecast holds
/// only the rest of the day; with this, today's low and high are of more of it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Seen {
    pub day: i64,
    pub low: f64,
    pub high: f64,
    pub code: Option<i64>,
}

impl Seen {
    fn note(&mut self, at: &DateTime, temperature: Option<f64>, code: Option<i64>) {
        let day = at.toordinal();
        if self.day != day {
            *self = Seen {
                day,
                low: f64::INFINITY,
                high: f64::NEG_INFINITY,
                code: None,
            };
        }
        if let Some(temperature) = temperature {
            self.low = self.low.min(temperature);
            self.high = self.high.max(temperature);
        }
        self.code = severer(self.code, code);
    }
}

/// The free plan's present weather (`/2.5/weather`) and forecast (`/2.5/forecast`) as
/// Open-Meteo's answer, `now` a Unix time; `seen` keeps what was seen of today.
pub fn from_free(
    current_data: &Dict,
    forecast: &Dict,
    imperial: bool,
    now: f64,
    seen: &mut Seen,
) -> Dict {
    let city = forecast.get("city").and_then(Value::as_dict);
    let offset = match current_data.get("timezone") {
        Some(value) => offset_of(Some(value)),
        None => offset_of(city.and_then(|city| city.get("timezone"))),
    };
    let mut out = Dict::default();
    out.insert("utc_offset_seconds".into(), Value::int(offset));
    let temperature = number(current_data, &["main", "temp"]);
    let (code_now, day_now) = condition(current_data);
    let (values, named) = current(
        temperature,
        number(current_data, &["main", "feels_like"]),
        number(current_data, &["main", "humidity"]),
        number(current_data, &["wind", "speed"]),
        (code_now, day_now),
        imperial,
    );
    out.insert("current".into(), values);
    out.insert("current_units".into(), named);
    let Some(local_now) = local(now, offset) else {
        return out;
    };
    seen.note(&local_now, temperature, code_now);

    // the steps: now (the present weather), then the forecast's
    struct Step {
        t: f64,
        temperature: Option<f64>,
        code: Option<i64>,
        rain: Option<f64>,
        day: Option<bool>,
    }
    let mut steps = vec![Step {
        t: now,
        temperature,
        code: code_now,
        rain: None,
        day: day_now,
    }];
    for entry in list(forecast, "list").iter().filter_map(Value::as_dict) {
        let Some(t) = number(entry, &["dt"]).filter(|t| *t > now) else {
            continue;
        };
        let (code, icon_day) = condition(entry);
        let pod = entry
            .get("sys")
            .and_then(Value::as_dict)
            .and_then(|sys| sys.get("pod"))
            .and_then(Value::as_str)
            .map(|pod| pod == "d");
        steps.push(Step {
            t,
            temperature: number(entry, &["main", "temp"]),
            code,
            rain: number(entry, &["pop"]),
            day: pod.or(icon_day),
        });
    }
    steps.sort_by(|a, b| a.t.total_cmp(&b.t));

    // the hours: from the present one's start to the last step, between the steps
    let sun = city.and_then(|city| {
        let rise = number(city, &["sunrise"])?;
        let set = number(city, &["sunset"])?;
        (rise > 0.0 && set > rise && set - rise < 86400.0).then_some((rise, set))
    });
    let start = ((now + offset as f64) / 3600.0).floor() * 3600.0 - offset as f64;
    let last = steps.last().map_or(now, |step| step.t);
    let mut hours = Vec::new();
    let mut t = start;
    while t <= last && steps.len() > 1 {
        let after = steps
            .iter()
            .position(|step| step.t >= t)
            .unwrap_or(steps.len() - 1);
        let before = after.saturating_sub(1);
        let (a, b) = (&steps[before], &steps[after]);
        let temperature = match (a.temperature, b.temperature) {
            (Some(x), Some(y)) if b.t > a.t => {
                let share = ((t - a.t) / (b.t - a.t)).clamp(0.0, 1.0);
                Some(x + (y - x) * share)
            }
            (x, y) => y.or(x),
        };
        let near = if t - a.t <= b.t - t { a } else { b };
        let rain = near.rain.or(b.rain);
        let day = match sun {
            // the sun's times of today, a day further for each day ahead
            Some((rise, set)) => {
                let since = (t - rise).rem_euclid(86400.0);
                Some(since < set - rise)
            }
            None => near.day,
        };
        if let Some(at) = local(t, offset) {
            hours.push(Hour {
                at,
                temperature,
                code: near.code,
                rain,
                day,
            });
        }
        t += 3600.0;
    }
    if !hours.is_empty() {
        let (rows, named) = hourly(&hours, imperial);
        out.insert("hourly".into(), rows);
        out.insert("hourly_units".into(), named);
    }

    // the days: today with what was seen of it, the others where the forecast holds all of them
    let today = local_now.toordinal();
    let mut days: Vec<(Day, usize)> = Vec::new();
    for step in &steps {
        let Some(at) = local(step.t, offset).map(|at| at.midnight()) else {
            continue;
        };
        let Some(temperature) = step.temperature else {
            continue;
        };
        match days
            .iter_mut()
            .find(|(day, _)| day.at.toordinal() == at.toordinal())
        {
            Some((day, count)) => {
                day.high = day.high.max(temperature);
                day.low = day.low.min(temperature);
                day.code = severer(day.code, step.code);
                day.rain = match (day.rain, step.rain) {
                    (Some(x), Some(y)) => Some(x.max(y)),
                    (x, y) => x.or(y),
                };
                *count += 1;
            }
            None => days.push((
                Day {
                    at,
                    high: temperature,
                    low: temperature,
                    code: step.code,
                    rain: step.rain,
                },
                1,
            )),
        }
    }
    let mut kept = Vec::new();
    for (mut day, count) in days {
        let n = day.at.toordinal() - today;
        if n == 0 {
            if seen.day == today && seen.low.is_finite() {
                day.low = day.low.min(seen.low);
                day.high = day.high.max(seen.high);
            }
            day.code = severer(day.code, seen.code);
        } else if count < 8 || !(0..DAYS).contains(&n) {
            continue; // (not all of it: its high and low would be of a part)
        }
        kept.push(day);
    }
    if !kept.is_empty() {
        let (rows, named) = daily(&kept, imperial);
        out.insert("daily".into(), rows);
        out.insert("daily_units".into(), named);
    }
    out
}

/// The address of one of OpenWeatherMap's answers for a place.
pub fn url(
    base: &str,
    path: &str,
    latitude: f64,
    longitude: f64,
    imperial: bool,
    key: &str,
) -> String {
    let mut params = vec![
        ("lat", float_repr(latitude)),
        ("lon", float_repr(longitude)),
        (
            "units",
            if imperial { "imperial" } else { "metric" }.to_string(),
        ),
    ];
    if path.ends_with("onecall") {
        params.push(("exclude", "alerts".to_string()));
    }
    params.push(("appid", key.to_string()));
    let query: Vec<String> = params
        .iter()
        .map(|(name, value)| format!("{name}={}", quote_plus(value)))
        .collect();
    format!("{base}/{path}?{}", query.join("&"))
}

/// Why an answer is none, in words, the key never among them.
fn reason(why: &lp_net::Error, key: &str) -> String {
    let said = match why.status {
        Some(401) => "OpenWeatherMap does not take the API key (HTTP 401; a new key can take a \
                      few hours before it works)"
            .to_string(),
        Some(429) => "OpenWeatherMap: too many calls with this API key (HTTP 429)".to_string(),
        Some(status) => format!("OpenWeatherMap answered HTTP {status}"),
        None => format!("OpenWeatherMap: {}", why.text),
    };
    if key.is_empty() {
        said
    } else {
        said.replace(key, "…")
    }
}

/// OpenWeatherMap's weather for a place, asked for with the user's key: One Call 3.0 while the
/// key has it, else the free plan's two answers.
pub struct Client {
    pub base: String,
    pub key: String,
    pub latitude: f64,
    pub longitude: f64,
    pub imperial: bool,
    pub agent: String,
    /// whether the key has One Call 3.0 (None: not known yet)
    pub onecall: Option<bool>,
    pub seen: Seen,
}

impl Client {
    fn get(&self, path: &str) -> Result<Dict, lp_net::Error> {
        let address = url(
            &self.base,
            path,
            self.latitude,
            self.longitude,
            self.imperial,
            &self.key,
        );
        let data = lp_net::get(&address, &[("User-Agent", &self.agent)], 10.0, 16 << 20)?;
        let failed = |text: String| lp_net::Error { status: None, text };
        let text = String::from_utf8(data).map_err(|why| failed(why.to_string()))?;
        match lp_py::json::loads(&text) {
            Ok(Value::Dict(answer)) => Ok(answer),
            Ok(other) => Err(failed(format!(
                "the answer is no table but a {}",
                other.type_name()
            ))),
            Err(why) => Err(failed(why.to_string())),
        }
    }

    /// The weather of now (`now` a Unix time) as Open-Meteo's answer, and what is worth saying
    /// of how it was got; why there is none.
    pub fn fetch(&mut self, now: f64, notes: &mut Vec<String>) -> Result<Dict, String> {
        // (401: the key, or One Call not open to it; the free plan's answer tells which)
        let mut refused = false;
        if self.onecall != Some(false) {
            match self.get("3.0/onecall") {
                Ok(data) => {
                    self.onecall = Some(true);
                    return Ok(from_onecall(&data, self.imperial, now));
                }
                Err(why) if why.status == Some(401) => refused = true,
                Err(why) if self.onecall == Some(true) => return Err(reason(&why, &self.key)),
                Err(_) => {}
            }
        }
        let current = self
            .get("2.5/weather")
            .map_err(|why| reason(&why, &self.key))?;
        let forecast = self
            .get("2.5/forecast")
            .map_err(|why| reason(&why, &self.key))?;
        if refused {
            // the key works, One Call is not open to it: not asked again while the app runs
            self.onecall = Some(false);
            notes.push(
                "OpenWeatherMap: this API key has no One Call 3.0, so the weather comes from the \
                 free plan's forecast in 3-hour steps (no rain of the next two hours)"
                    .to_string(),
            );
        }
        Ok(from_free(
            &current,
            &forecast,
            self.imperial,
            now,
            &mut self.seen,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dict(json: &str) -> Dict {
        match lp_py::json::loads(json).unwrap() {
            Value::Dict(d) => d,
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn the_conditions_are_the_wmo_codes_of_the_same_weather() {
        let pairs = [
            (800, 0),
            (801, 1),
            (802, 2),
            (803, 3),
            (804, 3),
            (500, 61),
            (501, 63),
            (502, 65),
            (511, 66),
            (520, 80),
            (521, 81),
            (522, 82),
            (600, 71),
            (601, 73),
            (602, 75),
            (611, 67),
            (620, 85),
            (622, 86),
            (701, 45),
            (741, 45),
            (771, 82),
            (781, 95),
            (211, 95),
            (300, 51),
            (301, 53),
            (302, 55),
        ]; // fmt: skip
        for (id, code) in pairs {
            assert_eq!(wmo_code(id), code, "{id}");
        }
    }

    #[test]
    fn the_key_is_in_no_reason() {
        let why = lp_net::Error {
            status: None,
            text: "failed to reach https://x/?appid=SECRET123".into(),
        };
        assert!(!reason(&why, "SECRET123").contains("SECRET123"));
        let refused = lp_net::Error {
            status: Some(401),
            text: String::new(),
        };
        assert!(reason(&refused, "SECRET123").contains("HTTP 401"));
    }

    #[test]
    fn the_free_forecast_s_hours_lie_between_its_steps() {
        // 2026-11-19 12:20 UTC, at UTC; steps at 15:00 and 18:00
        let now = 1_795_090_800.0;
        let current = dict(
            r#"{"main": {"temp": 10.0, "feels_like": 8.04, "humidity": 70},
                "wind": {"speed": 5.0}, "weather": [{"id": 500, "icon": "10d"}], "timezone": 0}"#,
        );
        let forecast = dict(&format!(
            r#"{{"city": {{"timezone": 0, "sunrise": {rise}, "sunset": {set}}},
                "list": [{{"dt": {a}, "main": {{"temp": 13.0}}, "weather": [{{"id": 800}}],
                           "pop": 0.25, "sys": {{"pod": "d"}}}},
                         {{"dt": {b}, "main": {{"temp": 7.0}}, "weather": [{{"id": 601}}],
                           "pop": 0.6, "sys": {{"pod": "n"}}}}]}}"#,
            rise = 1_795_072_200.0, // 07:10
            set = 1_795_104_000.0,  // 16:00
            a = 1_795_100_400.0,    // 15:00
            b = 1_795_111_200.0,    // 18:00
        ));
        let mut seen = Seen::default();
        let answer = from_free(&current, &forecast, false, now, &mut seen);
        let current = answer["current"].as_dict().unwrap();
        assert_eq!(current["temperature_2m"], Value::Float(10.0));
        assert_eq!(current["apparent_temperature"], Value::Float(8.0));
        assert_eq!(current["wind_speed_10m"], Value::Float(18.0)); // 5 m/s
        assert_eq!(current["weather_code"], Value::int(61));
        assert_eq!(current["is_day"], Value::int(1));
        let hourly = answer["hourly"].as_dict().unwrap();
        let times: Vec<&str> = hourly["time"]
            .as_list()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        assert_eq!(times.first(), Some(&"2026-11-19T12:00"));
        assert_eq!(times.last(), Some(&"2026-11-19T18:00"));
        let temps: Vec<Value> = hourly["temperature_2m"].as_list().unwrap().to_vec();
        // 12:00 and 12:20 are now's; 13:00 a quarter of the way from now (10 at 12:20) to 13 at 15:00
        assert_eq!(temps[0], Value::Float(10.0));
        assert_eq!(temps[1], Value::Float(10.8));
        assert_eq!(temps[3], Value::Float(13.0));
        assert_eq!(temps[6], Value::Float(7.0));
        let codes = hourly["weather_code"].as_list().unwrap();
        assert_eq!(
            (codes[0].clone(), codes[3].clone(), codes[6].clone()),
            (Value::int(61), Value::int(0), Value::int(73))
        );
        let days = hourly["is_day"].as_list().unwrap();
        assert_eq!(days[3], Value::int(1)); // 15:00
        assert_eq!(days[4], Value::int(0)); // 16:00, the sun has set
                                            // today: from what was seen and the rest; tomorrow is not all there
        let daily = answer["daily"].as_dict().unwrap();
        assert_eq!(daily["time"].as_list().unwrap().len(), 1);
        assert_eq!(
            daily["temperature_2m_max"].as_list().unwrap()[0],
            Value::Float(13.0)
        );
        assert_eq!(
            daily["temperature_2m_min"].as_list().unwrap()[0],
            Value::Float(7.0)
        );
        assert_eq!(daily["weather_code"].as_list().unwrap()[0], Value::int(73));
        assert_eq!(
            daily["precipitation_probability_max"].as_list().unwrap()[0],
            Value::int(60)
        );
        assert!(!answer.contains_key("minutely_15"));
    }

    #[test]
    fn one_call_s_rain_by_the_minute_is_in_quarters() {
        let now = 1_795_089_600.0 + 5.0 * 60.0; // 12:05 UTC
        let minutes: Vec<String> = (0..60)
            .map(|m| {
                format!(
                    r#"{{"dt": {}, "precipitation": {}}}"#,
                    now + 60.0 * f64::from(m),
                    if m >= 25 { 1.2 } else { 0.0 }
                )
            })
            .collect();
        let hours: Vec<String> = (0..4)
            .map(|h| format!(r#"{{"dt": {}, "temp": 5, "pop": 0.5, "rain": {{"1h": 2.0}}, "weather": [{{"id": 500, "icon": "10d"}}]}}"#, 1_795_089_600.0 + 3600.0 * f64::from(h)))
            .collect();
        let data = dict(&format!(
            r#"{{"timezone_offset": 3600, "minutely": [{}], "hourly": [{}],
                "daily": [{{"dt": 1795100400, "temp": {{"min": 1, "max": 9}}, "pop": 0.8, "weather": [{{"id": 501}}]}}]}}"#,
            minutes.join(","),
            hours.join(",")
        ));
        let answer = from_onecall(&data, false, now);
        let rain = answer["minutely_15"].as_dict().unwrap();
        let times: Vec<&str> = rain["time"]
            .as_list()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap())
            .collect();
        // local (UTC+1): the step from 13:00 to 13:15 holds now
        assert_eq!(
            &times[..3],
            &["2026-11-19T13:15", "2026-11-19T13:30", "2026-11-19T13:45"]
        );
        let mm: Vec<f64> = rain["precipitation"]
            .as_list()
            .unwrap()
            .iter()
            .map(|v| v.to_f64().unwrap())
            .collect();
        // (UTC) 12:05 to 12:15 and 12:15 to 12:30 dry, 1.2 mm/h from 12:30: 0.3 mm a quarter;
        // 13:00 to 13:15 of its last five minutes, then the hours' 2 mm/h
        assert_eq!(&mm[..6], &[0.0, 0.0, 0.3, 0.3, 0.3, 0.5]);
        assert_eq!(times.len(), 12);
        assert_eq!(
            answer["daily"].as_dict().unwrap()["time"]
                .as_list()
                .unwrap()[0],
            Value::Str("2026-11-19".into())
        );
        assert_eq!(
            answer["hourly"].as_dict().unwrap()["time"]
                .as_list()
                .unwrap()[0],
            Value::Str("2026-11-19T13:00".into())
        );
    }
}
