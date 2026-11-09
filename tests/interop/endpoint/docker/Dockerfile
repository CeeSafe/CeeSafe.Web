# The quic-interop-runner image for CeeSafe.Web's server (tests/interop/endpoint; Web ADR 0014 §4, stage 5). Built by
# tests/interop/endpoint/docker/build.sh on a Linux host from the endpoint binary and the OpenSSL 3.5 it links, on the
# runner's endpoint base image (Ubuntu 24.04, whose own OpenSSL is 3.0).
FROM martenseemann/quic-network-simulator-endpoint:latest
COPY endpoint /endpoint
COPY lib/ /opt/openssl/lib/
COPY run_endpoint.sh /run_endpoint.sh
RUN chmod +x /run_endpoint.sh /endpoint
ENV LD_LIBRARY_PATH=/opt/openssl/lib
ENTRYPOINT ["/run_endpoint.sh"]
