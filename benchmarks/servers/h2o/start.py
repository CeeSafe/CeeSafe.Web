#!/usr/bin/env python3
"""Runs h2o as the comparison server: the routes of benchmarks/servers/conform.py's contract on 127.0.0.1:CW_PORT
(8090 by default), HTTP/1.1 and h2c with prior knowledge on one port, a thread per core (h2o's default).

usage: CW_PORT=18190 python3 benchmarks/servers/h2o/start.py [--h2o PATH]
       (default: $CW_H2O, else ./out/bin/h2o, which build.sh makes)

h2o has no handler for bytes held in memory, so the fixed bodies (/small, /4k, /64k) are files sent by its own file
handler, written here to a temporary directory with the configuration. /stream, /json and /echo are mruby handlers:
/stream yields its 16 KiB piece 64 times with no declared length, /json serialises its object for each request
(mruby-json, bundled with h2o), and /echo returns the request body (`limit-request-body`, 1 MiB). The process
replaces itself with h2o, so stopping it stops h2o.
"""
import argparse, os, pathlib, tempfile

HERE = pathlib.Path(__file__).resolve().parent


def alphabet(n):
    return bytes(97 + i % 26 for i in range(n))


CONF = """\
num-threads: {threads}
limit-request-body: 1048576
http2-idle-timeout: 60
listen:
  host: 127.0.0.1
  port: {port}
hosts:
  default:
    paths:
      /small:
        file.file: {files}/small
      /4k:
        file.file: {files}/4k
      /64k:
        file.file: {files}/64k
      /stream:
        mruby.handler: |
          piece = File.open("{files}/piece", "rb") {{ |f| f.read }}
          body = Object.new
          body.define_singleton_method(:each) {{ |&b| 64.times {{ b.call(piece) }} }}
          Proc.new {{ |env| [200, {{}}, body] }}
      /json:
        mruby.handler: |
          Proc.new {{ |env| [200, {{"content-type" => "application/json"}}, [JSON.generate({{"message" => "Hello, World!"}})]] }}
      /echo:
        mruby.handler: |
          Proc.new do |env|
            if env["REQUEST_METHOD"] == "POST"
              [200, {{}}, [env["rack.input"].read]]
            else
              [405, {{}}, []]
            end
          end
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--h2o", default=os.environ.get("CW_H2O") or str(HERE / "out" / "bin" / "h2o"))
    a = ap.parse_args()
    port = int(os.environ.get("CW_PORT", "8090"))
    work = pathlib.Path(tempfile.mkdtemp(prefix="bench-h2o-"))
    for name, body in (("small", b"hello, world\n"), ("4k", alphabet(4096)), ("64k", alphabet(65536)),
                       ("piece", alphabet(16384))):
        (work / name).write_bytes(body)
    conf = work / "h2o.conf"
    conf.write_text(CONF.format(port=port, files=work, threads=os.cpu_count() or 1))
    os.execv(a.h2o, [a.h2o, "-c", str(conf)])


if __name__ == "__main__":
    main()
