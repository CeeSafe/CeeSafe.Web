//! Axum: the routes of benchmarks/servers/conform.py's contract. `axum::serve` speaks HTTP/1.1 and h2c with prior
//! knowledge on one port (its `http1` and `http2` features), on Tokio's multi-threaded runtime (a worker per core).
//! With CW_CERT and CW_KEY, TLS on that port instead (rustls, tokio-rustls; ALPN h2, then http/1.1).

use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post};
use axum::serve::ListenerExt;
use bench_common::{bodies, ECHO_LIMIT, MESSAGE, STREAM_PIECES};
use futures_util::stream;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder;
use hyper_util::service::TowerToHyperService;
use std::convert::Infallible;
use std::sync::Arc;
use tokio_rustls::TlsAcceptor;

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
    if let Some(config) = bench_common::tls(&[b"h2", b"http/1.1"]) {
        return serve_tls(app, TlsAcceptor::from(Arc::new(config))).await;
    }
    let listener = tokio::net::TcpListener::bind(bench_common::address()).await?.tap_io(|tcp| {
        let _ = tcp.set_nodelay(true);
    });
    axum::serve(listener, app).await
}

/// TLS: `axum::serve` has none, so the router is served as Axum's own low-level TLS example does, with hyper-util's
/// auto builder over tokio-rustls (the builder `axum::serve` uses).
async fn serve_tls(app: Router, acceptor: TlsAcceptor) -> std::io::Result<()> {
    let listener = tokio::net::TcpListener::bind(bench_common::address()).await?;
    loop {
        let (socket, _) = listener.accept().await?;
        let _ = socket.set_nodelay(true);
        let acceptor = acceptor.clone();
        let service = TowerToHyperService::new(app.clone());
        tokio::spawn(async move {
            if let Ok(stream) = acceptor.accept(socket).await {
                let _ = Builder::new(TokioExecutor::new())
                    .serve_connection_with_upgrades(TokioIo::new(stream), service)
                    .await;
            }
        });
    }
}
