//! What every Rust comparison server shares: the address, and the bodies of benchmarks/servers/conform.py's contract
//! (the same as benchmarks/http's CeeSafe.Web server).

use bytes::Bytes;
use serde::Serialize;
use std::net::SocketAddr;
use std::sync::OnceLock;

/// 127.0.0.1:CW_PORT, 8090 by default (as benchmarks/http's server).
pub fn address() -> SocketAddr {
    let port = std::env::var("CW_PORT").ok().and_then(|p| p.parse::<u16>().ok()).filter(|p| *p > 0).unwrap_or(8090);
    SocketAddr::from(([127, 0, 0, 1], port))
}

/// `count` bytes of a repeating alphabet: byte i is 'a' + i % 26.
fn alphabet(count: usize) -> Bytes {
    Bytes::from((0..count).map(|i| b'a' + (i % 26) as u8).collect::<Vec<u8>>())
}

pub struct Bodies {
    pub small: Bytes,
    pub four_k: Bytes,
    pub sixty_four_k: Bytes,
    /// /stream's piece: 16 KiB, written 64 times.
    pub piece: Bytes,
}

/// Made once; each response shares them (`Bytes` clones a pointer), as the CeeSafe.Web server sends a body made once.
pub fn bodies() -> &'static Bodies {
    static B: OnceLock<Bodies> = OnceLock::new();
    B.get_or_init(|| Bodies {
        small: Bytes::from_static(b"hello, world\n"),
        four_k: alphabet(4096),
        sixty_four_k: alphabet(65536),
        piece: alphabet(16384),
    })
}

pub const STREAM_PIECES: usize = 64;

/// The most /echo reads, as the CeeSafe.Web server's `maxBodyBytes`.
pub const ECHO_LIMIT: usize = 1 << 20;

/// /json's object, serialised for each request.
#[derive(Serialize)]
pub struct Message {
    pub message: &'static str,
}

pub const MESSAGE: Message = Message { message: "Hello, World!" };
