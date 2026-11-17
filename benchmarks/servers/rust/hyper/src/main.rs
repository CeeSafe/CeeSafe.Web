//! Hyper alone (no framework): the routes of benchmarks/servers/conform.py's contract, HTTP/1.1 and h2c with prior
//! knowledge on one port (hyper-util's auto builder), on Tokio's multi-threaded runtime (a worker per core).

use bench_common::{bodies, ECHO_LIMIT, MESSAGE, STREAM_PIECES};
use bytes::Bytes;
use futures_util::stream;
use http_body_util::{BodyExt, Full, Limited, StreamBody, combinators::BoxBody};
use hyper::body::{Frame, Incoming};
use hyper::header::CONTENT_TYPE;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder;
use std::convert::Infallible;
use tokio::net::TcpListener;

type Body = BoxBody<Bytes, Infallible>;

fn full(b: Bytes) -> Body {
    Full::new(b).boxed()
}

fn status(code: StatusCode) -> Response<Body> {
    let mut r = Response::new(full(Bytes::new()));
    *r.status_mut() = code;
    r
}

async fn route(req: Request<Incoming>) -> Result<Response<Body>, Infallible> {
    let b = bodies();
    Ok(match (req.method(), req.uri().path()) {
        (&Method::GET, "/small") => Response::new(full(b.small.clone())),
        (&Method::GET, "/4k") => Response::new(full(b.four_k.clone())),
        (&Method::GET, "/64k") => Response::new(full(b.sixty_four_k.clone())),
        (&Method::GET, "/stream") => {
            let piece = b.piece.clone();
            let frames = stream::iter((0..STREAM_PIECES).map(move |_| Ok::<_, Infallible>(Frame::data(piece.clone()))));
            Response::new(StreamBody::new(frames).boxed())
        }
        (&Method::GET, "/json") => {
            let body = serde_json::to_vec(&MESSAGE).expect("a fixed object serialises");
            let mut r = Response::new(full(Bytes::from(body)));
            r.headers_mut().insert(CONTENT_TYPE, "application/json".parse().unwrap());
            r
        }
        (&Method::POST, "/echo") => match Limited::new(req.into_body(), ECHO_LIMIT).collect().await {
            Ok(collected) => Response::new(full(collected.to_bytes())),
            Err(_) => status(StatusCode::PAYLOAD_TOO_LARGE),
        },
        _ => status(StatusCode::NOT_FOUND),
    })
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    bodies();
    let listener = TcpListener::bind(bench_common::address()).await?;
    loop {
        let (socket, _) = listener.accept().await?;
        let _ = socket.set_nodelay(true);
        tokio::spawn(async move {
            let _ = Builder::new(TokioExecutor::new()).serve_connection(TokioIo::new(socket), service_fn(route)).await;
        });
    }
}
