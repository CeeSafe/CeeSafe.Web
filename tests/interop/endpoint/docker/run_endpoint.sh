#!/bin/bash
# The runner's entry point (quic-network-simulator): set up the simulated network's routes, then run the server. The
# endpoint exits with 127 for a role or test case it does not support.
set -e
/setup.sh
exec /endpoint
