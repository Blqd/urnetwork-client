# URnetwork client for Ubuntu — build, test and install helpers.
#
# The Go modules in this repo ask for a newer toolchain than the distro ships
# (lib/connect needs 1.25.0, lib/proxy 1.25.5, cmd/urnetwork-vpn 1.26), so the
# Go wrapper is pinned here.  Override it with: make GO=/usr/local/go/bin/go
# Debian/Ubuntu ship Go 1.18 as /usr/bin/go, which predates GOTOOLCHAIN and
# silently ignores it.  Prefer a newer local install when it exists.
GO ?= $(shell if /usr/local/go/bin/go version >/dev/null 2>&1; \
              then echo /usr/local/go/bin/go; else echo go; fi)
GOTOOLCHAIN ?= go1.26.0
GOCACHE     ?= /tmp/urnetwork-go-cache
BUILD_DIR   ?= build

export GOTOOLCHAIN GOCACHE

.PHONY: all build vpn socks test test-go test-gui test-scripts \
        fmt vet lint install install-desktop clean run-socks run-vpn

all: build

build: vpn socks

vpn:
	@mkdir -p $(BUILD_DIR)
	cd cmd/urnetwork-vpn && $(GO) build -trimpath -o ../../$(BUILD_DIR)/urnetwork-vpn .

socks:
	@mkdir -p $(BUILD_DIR)
	cd lib/proxy && $(GO) build -trimpath -o ../../$(BUILD_DIR)/socksproxy ./socks

# The lib/connect integration tests bind fixed local ports and are not safe to
# run in parallel with anything else, so the default target only covers the
# client module and the GUI/script tests.
test: test-go test-gui test-scripts

test-go:
	cd cmd/urnetwork-vpn && $(GO) test -timeout 120s ./...

test-gui:
	python3 -m pytest tests -q

test-scripts:
	bash -n scripts/urnetwork-connect.sh
	bash -n scripts/urnetwork-askpass.sh
	bash -n scripts/urnetwork-gui-launch.sh
	bash -n scripts/install-nopasswd.sh
	bash -n scripts/install-desktop-entry.sh
	python3 -m py_compile scripts/urnetwork-credentials.py

fmt:
	gofmt -w cmd/urnetwork-vpn lib/proxy/socks lib/proxy/tun.go \
	         lib/connect/ip.go lib/connect/jwt.go lib/connect/net_tls.go

vet:
	cd cmd/urnetwork-vpn && $(GO) vet ./...
	cd lib/proxy && $(GO) vet ./...

lint: fmt vet test-scripts

# Installs a root-owned copy of the launcher plus the built binaries and the
# narrow sudoers rule (see scripts/install-nopasswd.sh for the rationale).
install:
	scripts/install-nopasswd.sh

install-desktop:
	scripts/install-desktop-entry.sh

run-socks: socks
	USER_AUTH="$${USER_AUTH:?set USER_AUTH}" PASSWORD="$${PASSWORD:?set PASSWORD}" \
		$(BUILD_DIR)/socksproxy

run-vpn: vpn
	sudo scripts/urnetwork-connect.sh vpn

clean:
	rm -rf $(BUILD_DIR) *.tar.gz
