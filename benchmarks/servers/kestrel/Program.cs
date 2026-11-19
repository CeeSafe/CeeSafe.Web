// Kestrel with minimal APIs: the routes of benchmarks/servers/conform.py's contract, HTTP/1.1 on 127.0.0.1:CW_PORT
// and h2c on CW_H2C_PORT (CW_PORT + 1 by default). Kestrel cannot serve both on one cleartext endpoint: with
// Http1AndHttp2 and no TLS it speaks HTTP/1.1 only, since without ALPN it does not look for HTTP/2's preface, so h2c
// (prior knowledge) needs an endpoint whose protocol is Http2 alone. With CW_CERT and CW_KEY (PEM), one endpoint on
// CW_PORT speaks TLS instead (SslStream: OpenSSL on Linux), HTTP/1.1 and HTTP/2 chosen by ALPN.
using System.Security.Cryptography.X509Certificates;
using System.Text.Json.Serialization;
using Microsoft.AspNetCore.Server.Kestrel.Core;

static int Setting(string name, int fallback) =>
    int.TryParse(Environment.GetEnvironmentVariable(name), out var v) && v is > 0 and < 65536 ? v : fallback;

var port = Setting("CW_PORT", 8090);
var h2cPort = Setting("CW_H2C_PORT", port + 1);
var certFile = Environment.GetEnvironmentVariable("CW_CERT");
var keyFile = Environment.GetEnvironmentVariable("CW_KEY");
// A PEM key is ephemeral, which SslStream cannot use on Windows or macOS; a round trip through PKCS #12 makes it one
// the platform holds. On Linux it changes nothing.
X509Certificate2? certificate = null;
if (!string.IsNullOrEmpty(certFile) && !string.IsNullOrEmpty(keyFile))
{
    using var pem = X509Certificate2.CreateFromPemFile(certFile, keyFile);
    certificate = X509CertificateLoader.LoadPkcs12(pem.Export(X509ContentType.Pkcs12), null);
}

static byte[] Alphabet(int count)
{
    var b = new byte[count];
    for (var i = 0; i < count; i++) b[i] = (byte)('a' + i % 26);
    return b;
}

var small = "hello, world\n"u8.ToArray();
var fourK = Alphabet(4096);
var sixtyFourK = Alphabet(65536);
var piece = Alphabet(16384);
const int EchoLimit = 1 << 20;

var builder = WebApplication.CreateSlimBuilder(args);
builder.Logging.ClearProviders();
builder.Services.ConfigureHttpJsonOptions(o => o.SerializerOptions.TypeInfoResolverChain.Insert(0, AppJson.Default));
builder.WebHost.ConfigureKestrel(k =>
{
    k.AddServerHeader = false;
    k.Limits.MaxRequestBodySize = EchoLimit;
    if (certificate is not null)
    {
        k.Listen(System.Net.IPAddress.Loopback, port, l =>
        {
            l.Protocols = HttpProtocols.Http1AndHttp2;
            l.UseHttps(certificate);
        });
        return;
    }
    k.Listen(System.Net.IPAddress.Loopback, port, l => l.Protocols = HttpProtocols.Http1);
    k.Listen(System.Net.IPAddress.Loopback, h2cPort, l => l.Protocols = HttpProtocols.Http2);
});
var app = builder.Build();

app.MapGet("/small", () => Results.Bytes(small));
app.MapGet("/4k", () => Results.Bytes(fourK));
app.MapGet("/64k", () => Results.Bytes(sixtyFourK));
app.MapGet("/stream", async (HttpContext ctx) =>
{
    ctx.Response.StatusCode = 200;
    for (var i = 0; i < 64; i++) await ctx.Response.Body.WriteAsync(piece);
});
app.MapGet("/json", () => Results.Json(new Message("Hello, World!"), AppJson.Default.Message));
app.MapPost("/echo", async (HttpContext ctx) =>
{
    using var buffer = new MemoryStream(ctx.Request.ContentLength is long n and <= EchoLimit ? (int)n : 0);
    await ctx.Request.Body.CopyToAsync(buffer);
    ctx.Response.ContentLength = buffer.Length;
    await ctx.Response.Body.WriteAsync(buffer.GetBuffer().AsMemory(0, (int)buffer.Length));
});

app.Run();

record Message(string message);

[JsonSerializable(typeof(Message))]
partial class AppJson : JsonSerializerContext;
