//! Actix Web: the routes of benchmarks/servers/conform.py's contract, HTTP/1.1 and h2c with prior knowledge on one
//! port (the `http2` feature; `bind` detects h2c's preface by default since 4.x's `auto_h2c`), a worker per core
//! (Actix's default). With CW_CERT and CW_KEY, TLS on that port instead (rustls, Actix's `rustls-0_23` feature).

use actix_web::web::{self, Bytes, PayloadConfig};
use actix_web::{App, HttpResponse, HttpServer};
use bench_common::{bodies, ECHO_LIMIT, MESSAGE, STREAM_PIECES};
use futures_util::stream;
use std::convert::Infallible;

async fn stream_route() -> HttpResponse {
    let piece = bodies().piece.clone();
    HttpResponse::Ok().streaming(stream::iter((0..STREAM_PIECES).map(move |_| Ok::<_, Infallible>(piece.clone()))))
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    bodies();
    let server = HttpServer::new(|| {
        let b = bodies();
        App::new()
            .app_data(PayloadConfig::new(ECHO_LIMIT))
            .route("/small", web::get().to(move || async move { HttpResponse::Ok().body(b.small.clone()) }))
            .route("/4k", web::get().to(move || async move { HttpResponse::Ok().body(b.four_k.clone()) }))
            .route("/64k", web::get().to(move || async move { HttpResponse::Ok().body(b.sixty_four_k.clone()) }))
            .route("/stream", web::get().to(stream_route))
            .route("/json", web::get().to(|| async { web::Json(MESSAGE) }))
            .route("/echo", web::post().to(|body: Bytes| async move { HttpResponse::Ok().body(body) }))
    });
    // With CW_CERT and CW_KEY, TLS (rustls; Actix offers h2 and http/1.1 by ALPN itself).
    match bench_common::tls(&[]) {
        Some(config) => server.bind_rustls_0_23(bench_common::address(), config)?.run().await,
        None => server.bind(bench_common::address())?.run().await,
    }
}
