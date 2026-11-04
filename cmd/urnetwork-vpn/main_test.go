package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"golang.org/x/sys/unix"
)

func TestSystemTunWriteRawWhenNoPI(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	defer r.Close()
	defer w.Close()

	tun := &systemTun{fd: int(w.Fd()), noPI: true}
	packet := []byte{0x45, 0x00, 0x00, 0x54}

	n, err := tun.Write(packet)
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	if n != len(packet) {
		t.Fatalf("write size = %d, want %d", n, len(packet))
	}

	if err := w.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}
	got, err := io.ReadAll(r)
	if err != nil {
		t.Fatalf("read all: %v", err)
	}
	if !bytes.Equal(got, packet) {
		t.Fatalf("write payload = %x, want %x", got, packet)
	}
}

func TestSelectBypassCIDRsFiltersFamilyAndDuplicates(t *testing.T) {
	gw4 := netip.MustParseAddr("192.168.31.1")
	got := selectBypassCIDRs(gw4, []string{
		"65.49.70.85/32",
		"65.49.70.85/32",
		"2001:470:99:56::1/128",
		"bad-cidr",
	})

	want := []string{"65.49.70.85/32"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("selectBypassCIDRs = %v, want %v", got, want)
	}
}

func TestTunFlagsEncoding(t *testing.T) {
	var ifr [64]byte
	binary.LittleEndian.PutUint16(ifr[16:18], uint16(unix.IFF_TUN|unix.IFF_NO_PI))

	got := binary.LittleEndian.Uint16(ifr[16:18])
	want := uint16(unix.IFF_TUN | unix.IFF_NO_PI)
	if got != want {
		t.Fatalf("tun flags = %#x, want %#x", got, want)
	}
}

func TestSendPacketFailureDiagnosticIncludesPacketContext(t *testing.T) {
	ipv4TCPAck := []byte{
		0x45, 0x00, 0x00, 0x28,
		0x00, 0x00, 0x00, 0x00,
		0x40, 0x06, 0x00, 0x00,
		10, 0, 0, 1,
		107, 191, 238, 91,
		0xc0, 0x00, 0x01, 0xbb,
		0x00, 0x00, 0x00, 0x10,
		0x00, 0x00, 0x00, 0x20,
		0x50, 0x10, 0x00, 0x00,
		0x00, 0x00, 0x00, 0x00,
	}

	got := sendPacketFailureDiagnostic(ipv4TCPAck)

	want := "[TUN!!] SendPacket FAILED TCP 10.0.0.1:49152->107.191.238.91:443 len=40 probable=tcp-collapse/backpressure/no-client"
	if got != want {
		t.Fatalf("diagnostic = %q, want %q", got, want)
	}
}

func TestDefaultRouteArgsPinsTunSourceAddress(t *testing.T) {
	prefix := netip.MustParsePrefix("10.0.0.1/24")

	got := defaultRouteArgs("urnetwork", prefix)
	want := []string{"route", "replace", "default", "dev", "urnetwork", "src", "10.0.0.1", "metric", "10"}

	if !reflect.DeepEqual(got, want) {
		t.Fatalf("defaultRouteArgs = %v, want %v", got, want)
	}
}

func TestVpnMultiClientSettingsAllowTcpRetransmits(t *testing.T) {
	settings := vpnMultiClientSettings()

	if settings.TcpCollapsePrevention {
		t.Fatal("VPN multi-client settings should disable TcpCollapsePrevention")
	}
}

func TestValidateMtuRejectsOutOfRange(t *testing.T) {
	for _, mtu := range []int{0, 575, 9001} {
		if err := validateMtu(mtu); err == nil {
			t.Fatalf("validateMtu(%d) = nil, want error", mtu)
		}
	}
	for _, mtu := range []int{576, 1400, 9000} {
		if err := validateMtu(mtu); err != nil {
			t.Fatalf("validateMtu(%d) = %v, want nil", mtu, err)
		}
	}
}

func TestIPv6BlackholeArgs(t *testing.T) {
	wantAdd := []string{"-6", "route", "add", "blackhole", "default", "metric", "10"}
	if got := ipv6BlackholeArgs(); !reflect.DeepEqual(got, wantAdd) {
		t.Fatalf("ipv6BlackholeArgs = %v, want %v", got, wantAdd)
	}
	wantDel := []string{"-6", "route", "del", "blackhole", "default", "metric", "10"}
	if got := ipv6BlackholeDelArgs(); !reflect.DeepEqual(got, wantDel) {
		t.Fatalf("ipv6BlackholeDelArgs = %v, want %v", got, wantDel)
	}
}

func TestDescribePacket(t *testing.T) {
	tcp := []byte{
		0x45, 0x00, 0x00, 0x28,
		0x00, 0x00, 0x00, 0x00,
		0x40, 0x06, 0x00, 0x00,
		10, 0, 0, 1,
		107, 191, 238, 91,
		0xc0, 0x00, 0x01, 0xbb,
	}
	if got, want := describePacket(tcp), "TCP 10.0.0.1:49152->107.191.238.91:443 (24 bytes)"; got != want {
		t.Fatalf("describePacket(tcp) = %q, want %q", got, want)
	}
	if got, want := describePacket([]byte{0x60, 0, 0, 0}), "IPv6 len=4"; got != want {
		t.Fatalf("describePacket(v6) = %q, want %q", got, want)
	}
}

func TestSystemTunReadReturnsIndependentCopy(t *testing.T) {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatalf("pipe: %v", err)
	}
	defer r.Close()
	defer w.Close()

	tun := &systemTun{fd: int(r.Fd()), mtu: 1400, noPI: true}
	payload := []byte{0x45, 0x00, 0x00, 0x14, 1, 2, 3, 4}
	if _, err := w.Write(payload); err != nil {
		t.Fatalf("write pipe: %v", err)
	}

	buf := make([]byte, 1500)
	got, err := tun.Read(buf)
	if err != nil {
		t.Fatalf("tun read: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("read payload = %x, want %x", got, payload)
	}
	for i := range buf {
		buf[i] = 0xff
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("read result aliases the shared buffer: %x", got)
	}
}

func TestPacketSenderEnqueuesDropsAndStops(t *testing.T) {
	stats := &trafficStats{}
	sent := make(chan []byte, 8)
	release := make(chan struct{})
	sender := newPacketSender(1, 1, time.Second, stats, false, func(packet []byte, _ time.Duration) bool {
		sent <- packet
		<-release
		return true
	})
	defer sender.Stop()

	// wait until the worker took "a", so the single queue slot is free for "b"
	if !sender.Enqueue([]byte("a")) {
		t.Fatal("first enqueue should succeed")
	}
	select {
	case <-sent:
	case <-time.After(2 * time.Second):
		t.Fatal("worker did not start in time")
	}

	if !sender.Enqueue([]byte("b")) {
		t.Fatal("second enqueue should occupy the free queue slot")
	}
	if sender.Enqueue([]byte("c")) {
		t.Fatal("enqueue beyond capacity should be dropped, not block")
	}
	if got := stats.txDropped.Load(); got != 1 {
		t.Fatalf("txDropped = %d, want 1", got)
	}

	close(release)
	select {
	case <-sent:
	case <-time.After(2 * time.Second):
		t.Fatal("queued packet was not sent in time")
	}

	sender.Stop()
	if sender.Enqueue([]byte("d")) {
		t.Fatal("enqueue after Stop should be rejected")
	}
}

func TestWriteStateFileWritesJSONWithPrivatePerms(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nested", "state.json")
	st := stateFile{
		Version:     1,
		Mode:        "vpn",
		Pid:         4242,
		Connected:   true,
		Provider:    "United States",
		TunName:     "urnetwork",
		TunAddr:     "10.0.0.1/24",
		Mtu:         1400,
		IPv6Blocked: true,
		StartedAt:   time.Unix(1700000000, 0).UTC(),
	}
	if err := writeStateFile(path, st); err != nil {
		t.Fatalf("writeStateFile: %v", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if got := info.Mode().Perm(); got != 0o600 {
		t.Fatalf("state file perm = %o, want 600", got)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if decoded["mode"] != "vpn" || decoded["ipv6_blocked"] != true {
		t.Fatalf("unexpected state JSON: %s", data)
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("temporary state file was left behind")
	}
}
