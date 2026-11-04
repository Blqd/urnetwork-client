package extender

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"os"
	"path/filepath"
	"time"

	"testing"

	"github.com/go-playground/assert/v2"

	"github.com/urnetwork/connect"
)

func TestExtender(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping testing in short mode")
	}

	// actual content server, port 443 (127.0.0.1)
	// https, self signed
	// one route, /hello

	// extender server, port 442

	// client

	// test uses extender http client to GET /hello

	settings := DefaultExtenderSettings()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	certPemBytes, keyPemBytes, err := selfSign([]string{"localhost"}, "Connect Test", settings.ValidFrom, settings.ValidFor)
	assert.Equal(t, err, nil)

	tempDirPath, err := os.MkdirTemp("", "connect")
	assert.Equal(t, err, nil)

	certFile := filepath.Join(tempDirPath, "localhost.pem")
	keyFile := filepath.Join(tempDirPath, "localhost.key")
	os.WriteFile(certFile, certPemBytes, 0x777)
	os.WriteFile(keyFile, keyPemBytes, 0x777)

	contentListener, err := net.Listen("tcp", ":0")
	assert.Equal(t, err, nil)
	contentPort := contentListener.Addr().(*net.TCPAddr).Port
	server := &http.Server{
		Handler: &testExtenderServer{},
	}
	defer server.Close()
	go server.ServeTLS(contentListener, certFile, keyFile)

	extenderPort := reserveTcpPort(t)
	extenderServer := NewExtenderServer(
		ctx,
		[]string{"montrose"},
		[]string{"localhost"},
		map[int][]connect.ExtenderConnectMode{
			extenderPort: []connect.ExtenderConnectMode{connect.ExtenderConnectModeTcpTls},
		},
		&net.Dialer{},
		settings,
	)
	defer extenderServer.Close()
	extenderErr := make(chan error, 1)
	go func() {
		extenderErr <- extenderServer.ListenAndServe()
	}()
	waitForTcpPort(t, "127.0.0.1", extenderPort, extenderErr)

	localIp, err := netip.ParseAddr("127.0.0.1")
	assert.Equal(t, err, nil)

	connectSettings := connect.DefaultConnectSettings()
	connectSettings.TlsConfig = &tls.Config{
		InsecureSkipVerify: true,
	}

	client := connect.NewExtenderHttpClient(
		connectSettings,
		&connect.ExtenderConfig{
			Profile: connect.ExtenderProfile{
				ConnectMode: connect.ExtenderConnectModeTcpTls,
				ServerName:  "bringyour.com",
				Port:        extenderPort,
			},
			Ip:     localIp,
			Secret: "montrose",
		},
	)

	r, err := client.Get(fmt.Sprintf("https://localhost:%d/hello", contentPort))

	assert.Equal(t, err, nil)
	assert.Equal(t, r.StatusCode, 200)

	body, err := io.ReadAll(r.Body)
	assert.Equal(t, err, nil)
	assert.Equal(t, string(body), "{}")

}

func reserveTcpPort(t *testing.T) int {
	t.Helper()

	listener, err := net.Listen("tcp", ":0")
	assert.Equal(t, err, nil)
	defer listener.Close()

	return listener.Addr().(*net.TCPAddr).Port
}

func waitForTcpPort(t *testing.T, host string, port int, errCh <-chan error) {
	t.Helper()

	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	deadline := time.Now().Add(3 * time.Second)
	for {
		select {
		case err := <-errCh:
			assert.Equal(t, err, nil)
			return
		default:
		}

		conn, err := net.DialTimeout("tcp", addr, 50*time.Millisecond)
		if err == nil {
			conn.Close()
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", addr)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

type testExtenderServer struct {
}

func (self *testExtenderServer) ServeHTTP(w http.ResponseWriter, req *http.Request) {

	w.Header().Add("Content-Type", "application/json")
	w.Write([]byte("{}"))
}
