//! A model server, on this computer or another of the network, as the AI board's third agent
//! (`ai.local.*`): llama.cpp's server, or Ollama. It is asked over HTTP, a few GETs every
//! couple of seconds while a theme shows `ai.local.*`; nothing is sent to it but the requests.
//!
//! - llama.cpp: `/slots` (empty while it has no model loaded) says of each slot whether it
//!   works (`is_processing`; older builds `state`), its task (`id_task`) and how far it is
//!   with it (`n_prompt_tokens_processed`, `next_token.n_decoded`); the model comes from a
//!   slot where it names one, else from `/v1/models`. A server started with `--no-slots`
//!   answers `/health` alone: it runs, idle.
//! - Ollama: `/api/ps` lists the models it holds; it does not say whether one works, so it is
//!   idle while it holds one.
//!
//! A server that holds no model (or loads it), or does not answer, is off.

use lp_py::{Dict, Value};

/// how often the server is asked, while a theme shows `ai.local.*`
pub const EVERY_S: f64 = 2.0;
/// how long each step of a request may take
const TIMEOUT_S: f64 = 2.0;
/// the most of an answer that is read
const LIMIT: usize = 1 << 20;
/// what llama.cpp's server answers while it loads its model
const LOADING: u16 = 503;

/// One slot of llama.cpp's server.
#[derive(Clone, Debug, PartialEq)]
pub struct Slot {
    pub id: i64,
    pub working: bool,
    /// the task it works on (-1: none)
    pub task: i64,
    /// tokens of that task done so far: the prompt's processed and those it made
    pub tokens: f64,
}

/// What the server said.
#[derive(Clone, Debug, PartialEq)]
pub struct Seen {
    /// "llama.cpp" or "Ollama"
    pub server: &'static str,
    pub model: String,
    pub slots: Vec<Slot>,
    /// whether it tells when it works (llama.cpp's `/slots`)
    pub tells_work: bool,
}

/// The address as it is asked: without a trailing "/", "http://" where it names no scheme.
pub fn base(url: &str) -> String {
    let url = url.trim().trim_end_matches('/');
    match url.contains("://") {
        true => url.to_string(),
        false => format!("http://{url}"),
    }
}

/// An answer's JSON; Err with the status where the server answered with an error.
fn get(base: &str, path: &str) -> Result<Value, (Option<u16>, String)> {
    let data = lp_net::get(&format!("{base}{path}"), &[], TIMEOUT_S, LIMIT)
        .map_err(|why| (why.status, why.text))?;
    let text = String::from_utf8_lossy(&data);
    lp_py::json::loads(&text).map_err(|_| (None, format!("{path}: no JSON")))
}

fn whole(value: Option<&Value>) -> Option<i64> {
    match value? {
        Value::Bool(_) => None,
        value => value.to_f64().map(|found| found as i64),
    }
}

fn number(value: Option<&Value>) -> f64 {
    match value {
        Some(Value::Bool(_)) | None => 0.0,
        Some(value) => value
            .to_f64()
            .filter(|found| found.is_finite())
            .unwrap_or(0.0),
    }
}

/// A model's name as it is shown: a file's name without its folder.
fn shown(name: &str) -> String {
    let name = name.rsplit(['/', '\\']).next().unwrap_or(name);
    name.chars().take(60).collect()
}

/// llama.cpp's `/slots`: its slots, and the model a slot names (none where none does).
pub fn slots_of(value: &Value) -> Option<(Vec<Slot>, String)> {
    let mut slots = Vec::new();
    let mut model = String::new();
    for (n, slot) in value.as_list()?.iter().enumerate() {
        let Some(slot) = slot.as_dict() else {
            continue;
        };
        let working = match slot.get("is_processing") {
            Some(Value::Bool(on)) => *on,
            _ => whole(slot.get("state")).is_some_and(|state| state != 0),
        };
        // (newer builds: next_token a list of one)
        let next = match slot.get("next_token") {
            Some(Value::List(items)) => items.first().and_then(Value::as_dict),
            Some(Value::Dict(next)) => Some(next),
            _ => None,
        };
        let made = number(next.and_then(|next| next.get("n_decoded")));
        let tokens = number(slot.get("n_prompt_tokens_processed")) + made;
        if model.is_empty() {
            for key in ["model", "id_model", "name"] {
                if let Some(Value::Str(name)) = slot.get(key) {
                    if !name.trim().is_empty() {
                        model = shown(name.trim());
                        break;
                    }
                }
            }
        }
        slots.push(Slot {
            id: whole(slot.get("id")).unwrap_or(n as i64),
            working,
            task: whole(slot.get("id_task")).unwrap_or(-1),
            tokens,
        });
    }
    Some((slots, model))
}

/// The first model of an OpenAI-like `/v1/models` (`data[].id`), or of Ollama's `/api/ps`
/// (`models[].name`); none where it lists none.
pub fn first_model(value: &Value, list: &str, key: &str) -> Option<String> {
    let models = value.as_dict()?.get(list)?.as_list()?;
    models
        .iter()
        .find_map(|model| match model.as_dict()?.get(key)? {
            Value::Str(name) if !name.trim().is_empty() => Some(shown(name.trim())),
            _ => None,
        })
}

fn models(base: &str) -> String {
    get(base, "/v1/models")
        .ok()
        .and_then(|value| first_model(&value, "data", "id"))
        .unwrap_or_default()
}

/// What the server at `url` says now: Ok(None) where it runs but holds no model, Err where it
/// does not answer (why). `model` is the model it named last, asked anew only where none is
/// known.
pub fn ask(url: &str, model: &str) -> Result<Option<Seen>, String> {
    let base = base(url);
    match get(&base, "/slots") {
        Ok(value) => {
            let Some((slots, named)) = slots_of(&value) else {
                return Err("/slots: no list".into());
            };
            if slots.is_empty() {
                return Ok(None); // (no model loaded)
            }
            let model = match (named.is_empty(), model.is_empty()) {
                (false, _) => named,
                (true, false) => model.to_string(),
                (true, true) => models(&base),
            };
            if model.is_empty() {
                return Ok(None);
            }
            return Ok(Some(Seen {
                server: "llama.cpp",
                model,
                slots,
                tells_work: true,
            }));
        }
        // (a server that answers, without slots: Ollama, or llama.cpp's with --no-slots)
        Err((Some(_), _)) => {}
        Err((None, why)) => return Err(why),
    }
    if let Ok(value) = get(&base, "/api/ps") {
        if value.as_dict().is_some_and(|ps| ps.contains_key("models")) {
            return Ok(first_model(&value, "models", "name").map(|model| Seen {
                server: "Ollama",
                model,
                slots: Vec::new(),
                tells_work: false,
            }));
        }
    }
    let health = match get(&base, "/health") {
        Ok(health) => health,
        // (llama.cpp's while it loads its model)
        Err((Some(LOADING), _)) => return Ok(None),
        Err((_, why)) => return Err(why),
    };
    let healthy = (health.as_dict())
        .and_then(|health: &Dict| health.get("status").map(Value::str))
        .is_some_and(|status| status == "ok");
    if !healthy {
        return Ok(None); // (loading its model, or none)
    }
    let model = match model.is_empty() {
        true => models(&base),
        false => model.to_string(),
    };
    Ok((!model.is_empty()).then_some(Seen {
        server: "llama.cpp",
        model,
        slots: Vec::new(),
        tells_work: false,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn json(text: &str) -> Value {
        lp_py::json::loads(text).unwrap()
    }

    #[test]
    fn llama_cpp_s_slots_say_who_works_on_what_and_how_far() {
        // (a live server's answer, as the issue gave it: no model in the slot)
        let idle = json(
            r#"[{"id":0,"is_processing":false,"id_task":-1,"n_prompt_tokens":0,
            "n_prompt_tokens_processed":0,"n_prompt_tokens_cache":0,
            "params":{"top_k":40,"top_p":1.0,"temp":0.8}}]"#,
        );
        let (slots, model) = slots_of(&idle).unwrap();
        assert_eq!(model, "");
        assert_eq!(
            slots,
            [Slot {
                id: 0,
                working: false,
                task: -1,
                tokens: 0.0
            }]
        );
        let busy = json(
            r#"[{"id":0,"is_processing":true,"id_task":7,"n_prompt_tokens_processed":512,
            "id_model":"models/Qwen3-8B-Q4_K_M.gguf","next_token":[{"n_decoded":40}]},
            {"id":1,"state":0,"id_task":-1,"next_token":{"n_decoded":3}}]"#,
        );
        let (slots, model) = slots_of(&busy).unwrap();
        assert_eq!(model, "Qwen3-8B-Q4_K_M.gguf");
        assert!(slots[0].working && slots[0].task == 7 && slots[0].tokens == 552.0);
        assert!(!slots[1].working && slots[1].tokens == 3.0);
        assert_eq!(slots_of(&json(r#"{"error": "nope"}"#)), None);
    }

    #[test]
    fn the_models_an_openai_like_server_and_ollama_list() {
        let openai = json(r#"{"object":"list","data":[{"id":"llama-3.2-3b","object":"model"}]}"#);
        assert_eq!(
            first_model(&openai, "data", "id").as_deref(),
            Some("llama-3.2-3b")
        );
        let ollama =
            json(r#"{"models":[{"name":"qwen3:8b","model":"qwen3:8b","size_vram":5000000000}]}"#);
        assert_eq!(
            first_model(&ollama, "models", "name").as_deref(),
            Some("qwen3:8b")
        );
        assert_eq!(
            first_model(&json(r#"{"models":[]}"#), "models", "name"),
            None
        );
        assert_eq!(base(" 192.168.1.20:8080/ "), "http://192.168.1.20:8080");
        assert_eq!(base("https://ai.local"), "https://ai.local");
    }

    /// A stand-in server on this computer answering each path as given (404 elsewhere), at
    /// most `count` requests; its address.
    fn server(answers: &'static [(&'static str, u16, &'static str)], count: usize) -> String {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap().to_string();
        std::thread::spawn(move || {
            for stream in listener.incoming().take(count) {
                let Ok(mut stream) = stream else { continue };
                let mut asked = Vec::new();
                let mut buffer = [0u8; 4096];
                while !asked.windows(4).any(|end| end == b"\r\n\r\n") {
                    match stream.read(&mut buffer) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => asked.extend_from_slice(&buffer[..n]),
                    }
                }
                let asked = String::from_utf8_lossy(&asked);
                let path = asked.split_whitespace().nth(1).unwrap_or("").to_string();
                let (status, body) = (answers.iter())
                    .find(|(at, ..)| *at == path)
                    .map_or((404, "Not Found"), |(_, status, body)| (*status, *body));
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n\
                     Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        address
    }

    #[test]
    fn llama_cpp_ollama_and_a_server_without_slots_are_asked_as_they_answer() {
        let llama = server(
            &[
                (
                    "/slots",
                    200,
                    r#"[{"id":0,"is_processing":true,"id_task":3,"n_prompt_tokens_processed":20,"next_token":[{"n_decoded":5}]}]"#,
                ),
                (
                    "/v1/models",
                    200,
                    r#"{"data":[{"id":"/models/gemma-3-4b.gguf"}]}"#,
                ),
            ],
            4,
        );
        let seen = ask(&llama, "").unwrap().unwrap();
        assert_eq!(
            (seen.server, seen.model.as_str()),
            ("llama.cpp", "gemma-3-4b.gguf")
        );
        assert!(seen.tells_work && seen.slots[0].working && seen.slots[0].tokens == 25.0);
        // (the model it named last: /v1/models is not asked again)
        assert_eq!(
            ask(&llama, "gemma-3-4b.gguf").unwrap().unwrap().model,
            "gemma-3-4b.gguf"
        );
        let ollama = server(
            &[("/api/ps", 200, r#"{"models":[{"name":"qwen3:8b"}]}"#)],
            2,
        );
        let seen = ask(&ollama, "").unwrap().unwrap();
        assert_eq!(
            (seen.server, seen.model.as_str(), seen.tells_work),
            ("Ollama", "qwen3:8b", false)
        );
        let empty = server(&[("/api/ps", 200, r#"{"models":[]}"#)], 2);
        assert_eq!(ask(&empty, ""), Ok(None)); // (it runs and holds no model)
        let unloaded = server(
            &[
                ("/slots", 200, "[]"),
                ("/v1/models", 200, r#"{"data":[{"id":"phi-4"}]}"#),
            ],
            1,
        );
        assert_eq!(ask(&unloaded, ""), Ok(None));
        let no_slots = server(
            &[
                (
                    "/slots",
                    501,
                    r#"{"error":{"message":"This server does not support slots endpoint."}}"#,
                ),
                ("/health", 200, r#"{"status":"ok"}"#),
                ("/v1/models", 200, r#"{"data":[{"id":"phi-4"}]}"#),
            ],
            4,
        );
        let seen = ask(&no_slots, "").unwrap().unwrap();
        assert_eq!(
            (seen.server, seen.model.as_str(), seen.tells_work),
            ("llama.cpp", "phi-4", false)
        );
        let loading = server(
            &[
                ("/slots", 503, "{}"),
                ("/health", 503, r#"{"error":{"message":"Loading model"}}"#),
            ],
            3,
        );
        assert_eq!(ask(&loading, ""), Ok(None));
        let wrong = server(&[], 3); // (another server at the address)
        assert!(ask(&wrong, "").unwrap_err().contains("404"));
        assert!(ask("127.0.0.1:1", "").is_err()); // (nobody listens)
    }
}
