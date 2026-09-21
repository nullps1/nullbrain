use axum::{extract::{DefaultBodyLimit, Path, State}, http::StatusCode, routing::get, Json, Router};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{env, sync::{Arc, Mutex}, time::Duration};

type Failure = (StatusCode, Json<Value>);
type Db = Arc<Mutex<Connection>>;

#[derive(Clone)]
struct App { db: Db, client: reqwest::Client, ollama: String, model: String }

#[derive(Debug, Serialize)]
struct Job {
    id: i64, status: String, prompt: String, model: String,
    created_at: String, started_at: Option<String>, finished_at: Option<String>,
    response: Option<String>, error: Option<String>,
}

#[derive(Deserialize)]
struct NewJob { prompt: String }

fn fail(status: StatusCode, message: impl ToString) -> Failure {
    (status, Json(json!({"error": message.to_string()})))
}

async fn database<T: Send + 'static>(db: Db, f: impl FnOnce(&mut Connection) -> rusqlite::Result<T> + Send + 'static) -> Result<T, Failure> {
    tokio::task::spawn_blocking(move || {
        let mut conn = db.lock().map_err(|_| "database lock poisoned".to_string())?;
        f(&mut conn).map_err(|e| e.to_string())
    }).await.map_err(|e| fail(StatusCode::INTERNAL_SERVER_ERROR, e))?
        .map_err(|e| fail(StatusCode::INTERNAL_SERVER_ERROR, e))
}

fn initialize(conn: &Connection) -> rusqlite::Result<()> {
    conn.busy_timeout(Duration::from_secs(5))?;
    conn.execute_batch("PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
          prompt TEXT NOT NULL, model TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
          started_at TEXT, finished_at TEXT, response TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS jobs_status_id ON jobs(status,id);
        UPDATE jobs SET status='failed', error='Service restarted during inference; submit a new job.',
          finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE status='running';")
}

fn read_job(row: &rusqlite::Row<'_>) -> rusqlite::Result<Job> {
    Ok(Job { id: row.get(0)?, status: row.get(1)?, prompt: row.get(2)?, model: row.get(3)?,
        created_at: row.get(4)?, started_at: row.get(5)?, finished_at: row.get(6)?,
        response: row.get(7)?, error: row.get(8)? })
}
const COLUMNS: &str = "id,status,prompt,model,created_at,started_at,finished_at,response,error";

fn claim(conn: &mut Connection) -> rusqlite::Result<Option<Job>> {
    let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
    let job = tx.query_row(&format!("SELECT {COLUMNS} FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"), [], read_job).optional()?;
    if let Some(ref j) = job {
        tx.execute("UPDATE jobs SET status='running', started_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?1", [j.id])?;
    }
    tx.commit()?;
    Ok(job)
}

async fn submit(State(app): State<App>, Json(input): Json<NewJob>) -> Result<(StatusCode, Json<Value>), Failure> {
    let prompt = input.prompt.trim().to_string();
    if prompt.is_empty() || prompt.len() > 2000 {
        return Err(fail(StatusCode::BAD_REQUEST, "prompt must contain 1 to 2000 UTF-8 bytes"));
    }
    let model = app.model.clone();
    let id = database(app.db, move |conn| {
        let tx = conn.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let active: i64 = tx.query_row("SELECT count(*) FROM jobs WHERE status IN ('queued','running')", [], |r| r.get(0))?;
        if active >= 16 { return Ok(None); }
        tx.execute("INSERT INTO jobs(status,prompt,model) VALUES('queued',?1,?2)", params![prompt, model])?;
        let id = tx.last_insert_rowid();
        tx.commit()?;
        Ok(Some(id))
    }).await?.ok_or_else(|| fail(StatusCode::TOO_MANY_REQUESTS, "queue is full"))?;
    Ok((StatusCode::ACCEPTED, Json(json!({"id":id,"status":"queued"}))))
}

async fn detail(State(app): State<App>, Path(id): Path<i64>) -> Result<Json<Job>, Failure> {
    database(app.db, move |conn| conn.query_row(&format!("SELECT {COLUMNS} FROM jobs WHERE id=?1"), [id], read_job).optional())
        .await?.map(Json).ok_or_else(|| fail(StatusCode::NOT_FOUND, "job not found"))
}

async fn list(State(app): State<App>) -> Result<Json<Vec<Job>>, Failure> {
    database(app.db, |conn| {
        let mut stmt = conn.prepare(&format!("SELECT {COLUMNS} FROM jobs ORDER BY id DESC LIMIT 50"))?;
        let rows = stmt.query_map([], read_job)?.collect();
        rows
    }).await.map(Json)
}

fn response_text(value: &Value) -> Result<String, String> {
    if value.get("done").and_then(Value::as_bool) != Some(true) {
        return Err("Ollama returned an incomplete response".into());
    }
    if value.get("done_reason").and_then(Value::as_str) == Some("length") {
        return Err("Model reached its output limit; try a smaller task".into());
    }
    value.pointer("/message/content").and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty()).map(String::from)
        .ok_or_else(|| "Ollama returned no text".into())
}

async fn infer(app: &App, job: &Job) -> Result<String, String> {
    let response = app.client.post(format!("{}/api/chat", app.ollama.trim_end_matches('/')))
        .json(&json!({
            "model":job.model,"stream":false,"keep_alive":"5m",
            "messages":[
                {"role":"system","content":"You are NullCode, a Java 21 coding assistant. Complete the requested small coding task concisely. You have no tools in this phase. Never claim you inspected files, compiled code, ran tests, or created a PR. Clearly distinguish suggested code from verified results."},
                {"role":"user","content":job.prompt}
            ],
            "options":{"num_ctx":2048,"num_predict":768,"temperature":0.2}
        })).send().await.map_err(|e| e.to_string())?
        .error_for_status().map_err(|e| e.to_string())?;
    let body: Value = response.json().await.map_err(|e| e.to_string())?;
    response_text(&body)
}

async fn worker(app: App) -> Result<(), Failure> {
    loop {
        let Some(job) = database(app.db.clone(), claim).await? else {
            tokio::time::sleep(Duration::from_secs(1)).await;
            continue;
        };
        eprintln!("job {} running", job.id);
        let result = infer(&app, &job).await;
        let (status, response, error) = match result {
            Ok(text) => ("succeeded", Some(text), None),
            Err(error) => ("failed", None, Some(error)),
        };
        let id = job.id;
        database(app.db.clone(), move |conn| conn.execute(
            "UPDATE jobs SET status=?1,response=?2,error=?3,finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?4",
            params![status,response,error,id])).await?;
        eprintln!("job {id} {status}");
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = env::var("NULLCODE_DB").unwrap_or_else(|_| "nullcode.sqlite3".into());
    let conn = Connection::open(path)?;
    initialize(&conn)?;
    let app = App {
        db: Arc::new(Mutex::new(conn)),
        client: reqwest::Client::builder().connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(900)).build()?,
        ollama: env::var("OLLAMA_URL").unwrap_or_else(|_| "http://127.0.0.1:11434".into()),
        model: env::var("OLLAMA_MODEL").unwrap_or_else(|_| "qwen2.5-coder:3b".into()),
    };
    let router = Router::new()
        .route("/health", get(|| async { Json(json!({"status":"ok","phase":"inference-only"})) }))
        .route("/jobs", get(list).post(submit))
        .route("/jobs/{id}", get(detail))
        .layer(DefaultBodyLimit::max(8192)).with_state(app.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:8080").await?;
    eprintln!("NullCode listening on 127.0.0.1:8080; one inference worker");
    tokio::select! {
        result = axum::serve(listener, router) => { result?; }
        result = worker(app) => { return Err(format!("worker stopped: {result:?}").into()); }
        _ = tokio::signal::ctrl_c() => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queue_claims_fifo_and_never_claims_a_running_job_twice() {
        let mut db = Connection::open_in_memory().unwrap();
        initialize(&db).unwrap();
        for prompt in ["first", "second"] {
            db.execute("INSERT INTO jobs(status,prompt,model) VALUES('queued',?1,'test')", [prompt]).unwrap();
        }
        assert_eq!(claim(&mut db).unwrap().unwrap().prompt, "first");
        assert_eq!(claim(&mut db).unwrap().unwrap().prompt, "second");
        assert!(claim(&mut db).unwrap().is_none());
    }

    #[test]
    fn restart_fails_interrupted_job_but_keeps_queued_work() {
        let mut db = Connection::open_in_memory().unwrap();
        initialize(&db).unwrap();
        for _ in 0..2 {
            db.execute("INSERT INTO jobs(status,prompt,model) VALUES('queued','task','test')", []).unwrap();
        }
        let interrupted = claim(&mut db).unwrap().unwrap().id;
        initialize(&db).unwrap();
        let (status, error): (String, Option<String>) = db.query_row(
            "SELECT status,error FROM jobs WHERE id=?1", [interrupted], |r| Ok((r.get(0)?,r.get(1)?))).unwrap();
        assert_eq!(status, "failed");
        assert!(error.is_some());
        assert!(claim(&mut db).unwrap().is_some());
    }

    #[test]
    fn model_errors_and_truncated_output_are_not_success() {
        assert!(response_text(&json!({"error":"missing model"})).is_err());
        assert!(response_text(&json!({"done":true,"message":{"content":" "}})).is_err());
        assert!(response_text(&json!({"done":true,"done_reason":"length","message":{"content":"partial"}})).is_err());
        assert_eq!(response_text(&json!({"done":true,"message":{"content":"class Example {}"}})).unwrap(), "class Example {}");
    }
}
