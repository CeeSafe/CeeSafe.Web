// Go crypto/tls interop for web_tls (Web ADR 0002 §5). Standard library only.
//
//	go run ./tests/interop/go client <port> <ca.pem>
//	    connects to 127.0.0.1:<port> as "localhost": TLS 1.3, then TLS 1.2 with each ECDHE-ECDSA AEAD suite Go has,
//	    then each CBC suite (which the server must refuse); prints one line per attempt.
//	go run ./tests/interop/go server <port> <cert.pem> <key.pem>
//	    serves HTTPS on 127.0.0.1:<port> ("hello from go"), TLS 1.2-1.3, ALPN http/1.1; exits after one request.
package main

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: client <port> <ca.pem> | server <port> <cert.pem> <key.pem>")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "client":
		client(os.Args[2], os.Args[3])
	case "server":
		server(os.Args[2], os.Args[3], os.Args[4])
	}
}

func client(port, caFile string) {
	pem, err := os.ReadFile(caFile)
	if err != nil {
		panic(err)
	}
	roots := x509.NewCertPool()
	roots.AppendCertsFromPEM(pem)
	try := func(name string, min, max uint16, suites []uint16) {
		cfg := &tls.Config{RootCAs: roots, ServerName: "localhost", MinVersion: min, MaxVersion: max,
			CipherSuites: suites, NextProtos: []string{"http/1.1"}}
		conn, err := tls.Dial("tcp", "127.0.0.1:"+port, cfg)
		if err != nil {
			fmt.Printf("%s refused %v\n", name, err)
			return
		}
		defer conn.Close()
		fmt.Fprintf(conn, "GET / HTTP/1.1\r\nhost: localhost\r\nconnection: close\r\n\r\n")
		body, _ := io.ReadAll(conn)
		st := conn.ConnectionState()
		ok := strings.HasSuffix(string(body), "hello, tls")
		fmt.Printf("%s ok %s %s alpn=%s answered=%v\n", name, tls.VersionName(st.Version),
			tls.CipherSuiteName(st.CipherSuite), st.NegotiatedProtocol, ok)
	}
	try("tls13", tls.VersionTLS13, tls.VersionTLS13, nil)
	for _, s := range tls.CipherSuites() {
		if strings.Contains(s.Name, "ECDSA") {
			try("tls12:"+s.Name, tls.VersionTLS12, tls.VersionTLS12, []uint16{s.ID})
		}
	}
	for _, s := range tls.InsecureCipherSuites() {
		if strings.Contains(s.Name, "ECDHE_ECDSA") {
			try("weak:"+s.Name, tls.VersionTLS12, tls.VersionTLS12, []uint16{s.ID})
		}
	}
}

func server(port, certFile, keyFile string) {
	cert, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		panic(err)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:"+port, &tls.Config{Certificates: []tls.Certificate{cert},
		MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"}})
	if err != nil {
		panic(err)
	}
	done := make(chan struct{})
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("connection", "close")
		fmt.Fprint(w, "hello from go")
		close(done)
	})}
	fmt.Println("go: listening")
	go srv.Serve(ln)
	<-done
	srv.Close()
}
