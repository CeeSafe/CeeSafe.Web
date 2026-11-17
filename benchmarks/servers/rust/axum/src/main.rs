//! Axum: the routes of benchmarks/servers/conform.py's contract. `axum::serve` speaks HTTP/1.1 and h2c with prior
//! knowledge on one port (its `http1` and `http2` features), on Tokio's multi-threaded runtime (a worker per core).

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post};
use axum::serve::ListenerExt;
use bench_common::{bodies, ECHO_LIMIT, MESSAGE, STREAM_PIECES};
use futures_util::stream;
use std::convert::Infallible;

async fn stream_route() -> Body {
    let piece = bodies().piece.clone();
    Body::from_stream(stream::iter((0..STREAM_PIECES).map(move |_| Ok::<_, Infallible>(piece.clone()))))
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    let b = bodies();
    let app = Router::new()
        .route("/small", get(|| async { b.small.clone() }))
        .route("/4k", get(|| async { b.four_k.clone() }))
        .route("/64k", get(|| async { b.sixty_four_k.clone() }))
        .route("/stream", get(stream_route))
        .route("/json", get(|| async { axum::Json(MESSAGE) }))
        .route("/echo", post(|body: Bytes| async move { body }))
        .layer(DefaultBodyLimit::max(ECHO_LIMIT));
    let listener = tokio::net::TcpListener::bind(bench_common::address()).await?.tap_io(|tcp| {
        let _ = tcp.set_nodelay(true);
    });
    axum::serve(listener, app).await
}
