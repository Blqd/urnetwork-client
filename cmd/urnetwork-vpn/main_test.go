package main

import (
	"bytes"
	"encoding/binary"
	"io"
	"net/netip"
	"os"
	"reflect"
	"testing"

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
