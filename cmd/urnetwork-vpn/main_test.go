package main

import (
	"bytes"
	"io"
	"net/netip"
	"os"
	"reflect"
	"testing"
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
