package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/netip"
	"os"
	"os/exec"
	"os/signal"
	"os/user"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	gojwt "github.com/golang-jwt/jwt/v5"
	"github.com/samber/lo"
	"github.com/urfave/cli/v2"
	"github.com/urnetwork/connect"
	"github.com/urnetwork/connect/protocol"
	"golang.org/x/sys/unix"
)

var Version string

const (
	statsInterval = 10 * time.Second
	sendTimeout   = 15 * time.Second
)

func init() {
	initGlog()
}

func initGlog() {
	flag.Set("stderrthreshold", "INFO")
	flag.Set("v", "3")
	os.Stderr = os.Stdout
}

type FindLocationsArgs struct {
	Query                     string  `json:"query"`
	MaxDistanceFraction       float32 `json:"max_distance_fraction,omitempty"`
	EnableMaxDistanceFraction bool    `json:"enable_max_distance_fraction,omitempty"`
}

type FindLocationsResult struct {
	Specs     *ProviderSpecList         `json:"specs"`
	Groups    json.RawMessage           `json:"groups"`
	Locations *LocationResultList       `json:"locations"`
	Devices   *LocationDeviceResultList `json:"devices"`
}

type exportedList[T any] struct {
	values []T
}

func (self *exportedList[T]) Values() []T {
	return self.values
}

func (self *exportedList[T]) UnmarshalJSON(b []byte) error {
	return json.Unmarshal(b, &self.values)
}

type ProviderSpecList struct{ exportedList[*ProviderSpec] }
type LocationGroupResultList struct {
	exportedList[*LocationGroupResult]
}
type LocationResultList struct{ exportedList[*LocationResult] }
type LocationDeviceResultList struct {
	exportedList[*LocationDeviceResult]
}

type ProviderSpec struct {
	LocationId      *connect.Id `json:"location_id,omitempty"`
	LocationGroupId *connect.Id `json:"location_group_id,omitempty"`
	ClientId        *connect.Id `json:"client_id,omitempty"`
}
type LocationGroupResult struct {
	LocationGroupId *connect.Id `json:"location_group_id"`
	Name            string      `json:"name"`
	ProviderCount   int         `json:"provider_count,omitempty"`
}
type LocationResult struct {
	LocationId    *connect.Id `json:"location_id"`
	LocationType  string      `json:"location_type"`
	Name          string      `json:"name"`
	ProviderCount int         `json:"provider_count,omitempty"`
}
type LocationDeviceResult struct {
	ClientId   *connect.Id `json:"client_id"`
	DeviceName string      `json:"device_name"`
}

type IdList struct{ exportedList[*connect.Id] }
type FindProvidersArgs struct {
	LocationId       *connect.Id `json:"location_id,omitempty"`
	LocationGroupId  *connect.Id `json:"location_group_id,omitempty"`
	Count            int         `json:"count"`
	ExcludeClientIds *IdList     `json:"exclude_location_ids,omitempty"`
}
type FindProvidersResult struct {
	ClientIds []*connect.Id `json:"client_ids,omitempty"`
}

func main() {
	cfg := struct {
		apiURL      string
		platformURL string
		userAuth    string
		password    string
		providerID  string
		city        string
		country     string
		region      string
		mtu         int
		tunName     string
		tunAddr     string
		noRoute     bool
		bypass      cli.StringSlice
		verbose     bool
		sendWorkers int
		sendQueue   int
		replaceTun  bool
		blockIPv6   bool
		stateFile   string
	}{}
	app := &cli.App{
		Flags: []cli.Flag{
			&cli.StringFlag{
				Name: "api-url", EnvVars: []string{"API_URL"},
				Destination: &cfg.apiURL, Value: "https://api.bringyour.com",
			},
			&cli.StringFlag{
				Name: "platform-url", EnvVars: []string{"PLATFORM_URL"},
				Destination: &cfg.platformURL, Value: "wss://connect.bringyour.com",
			},
			&cli.StringFlag{
				Name: "user-auth", Usage: "User auth (email or phone)",
				EnvVars: []string{"USER_AUTH"}, Destination: &cfg.userAuth, Required: true,
			},
			&cli.StringFlag{
				Name: "password", EnvVars: []string{"PASSWORD"},
				Destination: &cfg.password, Required: true,
			},
			&cli.StringFlag{
				Name: "provider-id", EnvVars: []string{"PROVIDER_ID"},
				Destination: &cfg.providerID,
			},
			&cli.StringFlag{
				Name: "city", EnvVars: []string{"CITY"}, Destination: &cfg.city,
			},
			&cli.StringFlag{
				Name: "country", EnvVars: []string{"COUNTRY"}, Destination: &cfg.country,
			},
			&cli.StringFlag{
				Name: "region", EnvVars: []string{"REGION"}, Destination: &cfg.region,
			},
			&cli.IntFlag{
				Name: "mtu", Value: 1400, Destination: &cfg.mtu,
			},
			&cli.StringFlag{
				Name: "tun-name", Value: "urnetwork", Destination: &cfg.tunName,
			},
			&cli.StringFlag{
				Name: "tun-addr", Usage: "TUN IP with prefix (e.g. 10.0.0.1/24)",
				Value: "10.0.0.1/24", Destination: &cfg.tunAddr,
			},
			&cli.BoolFlag{
				Name: "no-route", Usage: "Don't configure system routing",
				Destination: &cfg.noRoute,
			},
			&cli.StringSliceFlag{
				Name: "bypass", Usage: "Additional IPs/CIDRs to bypass TUN (can specify multiple)",
				Destination: &cfg.bypass,
			},
			&cli.BoolFlag{
				Name: "verbose", Usage: "Log every packet (slow; for troubleshooting only)",
				EnvVars: []string{"URNETWORK_VERBOSE"}, Destination: &cfg.verbose,
			},
			&cli.IntFlag{
				Name: "send-workers", Usage: "Number of concurrent packet senders",
				Value: 1, Destination: &cfg.sendWorkers,
			},
			&cli.IntFlag{
				Name: "send-queue", Usage: "Max packets buffered between TUN and the network",
				Value: 2048, Destination: &cfg.sendQueue,
			},
			&cli.BoolFlag{
				Name: "replace-tun", Usage: "Delete an existing TUN interface with the same name",
				Destination: &cfg.replaceTun,
			},
			&cli.BoolFlag{
				Name: "block-ipv6", Usage: "Block IPv6 while connected to prevent leaks",
				Value: true, Destination: &cfg.blockIPv6,
			},
			&cli.StringFlag{
				Name: "state-file", Usage: "Write connection state as JSON to this path",
				EnvVars: []string{"URNETWORK_STATE_FILE"}, Destination: &cfg.stateFile,
			},
		},
		Name: "urnetwork-vpn",
		Commands: []*cli.Command{
			{
				Name: "list-locations",
				Action: func(c *cli.Context) error {
					ctx := c.Context
					jwt, err := login(ctx, cfg.apiURL, cfg.userAuth, cfg.password)
					if err != nil {
						return fmt.Errorf("login failed: %w", err)
					}
					locations, err := getProviderLocations(ctx, cfg.apiURL, jwt)
					if err != nil {
						return fmt.Errorf("get locations failed: %w", err)
					}
					countries := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "country" })
					regions := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "region" })
					cities := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "city" })
					fmt.Println("countries:")
					for _, c := range countries {
						fmt.Printf("  %s\n", c.Name)
					}
					fmt.Println("regions:")
					for _, r := range regions {
						fmt.Printf("  %s\n", r.Name)
					}
					fmt.Println("cities:")
					for _, c := range cities {
						fmt.Printf("  %s\n", c.Name)
					}
					return nil
				},
			},
		},
		Action: func(c *cli.Context) error {
			ctx, cancel := context.WithCancel(c.Context)
			defer cancel()

			if err := validateMtu(cfg.mtu); err != nil {
				return err
			}
			// A root engine must never write or delete an arbitrary path just
			// because URNETWORK_STATE_FILE said so.
			cfg.stateFile = resolveStateFilePath(cfg.stateFile)

			jwt, err := login(ctx, cfg.apiURL, cfg.userAuth, cfg.password)
			if err != nil {
				return fmt.Errorf("login failed: %w", err)
			}

			locations, err := getProviderLocations(ctx, cfg.apiURL, jwt)
			if err != nil {
				return fmt.Errorf("get locations failed: %w", err)
			}

			providersSpec, providerName, err := getProviderSpec(locations, cfg.city, cfg.country, cfg.region, cfg.providerID)
			if err != nil {
				return fmt.Errorf("get provider spec failed: %w", err)
			}

			clientJWT, err := authNetworkClient(ctx, cfg.apiURL, jwt, &connect.AuthNetworkClientArgs{
				Description: "ubuntu vpn",
				DeviceSpec:  "vpn",
			})
			if err != nil {
				return fmt.Errorf("auth network client failed: %w", err)
			}

			clientID, err := parseByJwtClientId(clientJWT)
			if err != nil {
				return fmt.Errorf("parse client id failed: %w", err)
			}

			fmt.Println("my clientID:", clientID)

			generator := connect.NewApiMultiClientGenerator(
				ctx,
				providersSpec,
				connect.NewClientStrategyWithDefaults(ctx),
				[]connect.Id{clientID},
				cfg.apiURL,
				clientJWT,
				cfg.platformURL,
				"ubuntu vpn",
				"vpn",
				"0.0.0",
				&clientID,
				connect.DefaultClientSettings,
				connect.DefaultApiMultiClientGeneratorSettings(),
			)

			tunPrefix, err := netip.ParsePrefix(cfg.tunAddr)
			if err != nil {
				return fmt.Errorf("parse tun-addr failed: %w", err)
			}

			tun, err := createTun(cfg.tunName, tunPrefix, cfg.mtu, cfg.replaceTun)
			if err != nil {
				return fmt.Errorf("create tun failed: %w", err)
			}
			defer tun.CloseTun()

			stats := &trafficStats{}
			var addedBypasses []string
			ipv6Blocked := false
			if !cfg.noRoute {
				// Clone the flag's slice: StringSlice.Value() returns the
				// internal backing array, so appending to it is a footgun.
				bypassCIDRs := make([]string, 0, len(cfg.bypass.Value())+8)
				bypassCIDRs = append(bypassCIDRs, cfg.bypass.Value()...)
				bypassCIDRs = append(bypassCIDRs, resolveBypassHosts(ctx)...)
				var setupErr error
				addedBypasses, setupErr = setupRouting(cfg.tunName, tunPrefix, bypassCIDRs, cfg.mtu, cfg.blockIPv6)
				if setupErr != nil {
					// undo whatever the setup managed to configure before failing
					teardownRouting(cfg.tunName, tunPrefix, addedBypasses, ipv6Blocked)
					tun.CloseTun()
					return fmt.Errorf("setup routing failed: %w", setupErr)
				}
				ipv6Blocked = cfg.blockIPv6
				defer teardownRouting(cfg.tunName, tunPrefix, addedBypasses, ipv6Blocked)
			}

			source := connect.SourceId(clientID)
			startedAt := time.Now()

			receiveCallback := func(source connect.TransferPath, provideMode protocol.ProvideMode, ipPath *connect.IpPath, packet []byte) {
				if len(packet) == 0 {
					return
				}
				if cfg.verbose {
					fmt.Printf("[<<TUN] %s\n", describePacket(packet))
				}
				if _, err := tun.Write(packet); err != nil {
					stats.rxWriteErr.Add(1)
					fmt.Fprintln(os.Stderr, "tun write error:", err)
					return
				}
				stats.rxPackets.Add(1)
				stats.rxBytes.Add(uint64(len(packet)))
			}

			mc := connect.NewRemoteUserNatMultiClient(
				ctx,
				generator,
				receiveCallback,
				protocol.ProvideMode_Network,
				vpnMultiClientSettings(),
			)

			sender := newPacketSender(cfg.sendWorkers, cfg.sendQueue, sendTimeout, stats, cfg.verbose, func(packet []byte, timeout time.Duration) bool {
				return mc.SendPacket(source, protocol.ProvideMode_Network, packet, timeout)
			})
			defer sender.Stop()

			newState := func() stateFile {
				return stateFile{
					Version:       1,
					Mode:          "vpn",
					Pid:           os.Getpid(),
					Connected:     true,
					Provider:      providerName,
					TunName:       cfg.tunName,
					TunAddr:       tunPrefix.String(),
					Mtu:           cfg.mtu,
					IPv6Blocked:   ipv6Blocked,
					StartedAt:     startedAt,
					TxPackets:     stats.txPackets.Load(),
					TxBytes:       stats.txBytes.Load(),
					TxDropped:     stats.txDropped.Load(),
					TxFailed:      stats.txFailed.Load(),
					RxPackets:     stats.rxPackets.Load(),
					RxBytes:       stats.rxBytes.Load(),
					RxWriteErrors: stats.rxWriteErr.Load(),
				}
			}
			stateWarned := false
			writeState := func() {
				if cfg.stateFile == "" {
					return
				}
				if err := writeStateFile(cfg.stateFile, newState()); err != nil && !stateWarned {
					stateWarned = true
					fmt.Fprintf(os.Stderr, "warning: write state file %s: %v\n", cfg.stateFile, err)
				}
			}
			writeState()
			defer func() {
				if cfg.stateFile != "" {
					os.Remove(cfg.stateFile)
				}
			}()

			readErr := make(chan error, 1)
			go func() {
				// one reusable read buffer; Read returns a packet-sized copy
				buf := make([]byte, cfg.mtu+64)
				for {
					packet, err := tun.Read(buf)
					if err != nil {
						if ctx.Err() == nil {
							select {
							case readErr <- err:
							default:
							}
						}
						return
					}
					if len(packet) == 0 {
						continue
					}
					stats.txPackets.Add(1)
					stats.txBytes.Add(uint64(len(packet)))
					if cfg.verbose {
						fmt.Printf("[TUN>>] %s\n", describePacket(packet))
					}
					// Enqueue never blocks; it accounts a dropped packet itself.
					sender.Enqueue(packet)
				}
			}()

			go func() {
				ticker := time.NewTicker(statsInterval)
				defer ticker.Stop()
				for {
					select {
					case <-ticker.C:
						fmt.Println(stats.line(sender.queueLen(), cfg.sendQueue))
						writeState()
					case <-ctx.Done():
						return
					}
				}
			}()

			fmt.Printf("VPN connected. TUN: %s, IP: %s\n", cfg.tunName, tunPrefix)
			if ipv6Blocked {
				fmt.Println("IPv6 traffic is blocked while connected (disable with --block-ipv6=false).")
			}
			fmt.Println("Press Ctrl+C to disconnect.")

			sigCh := make(chan os.Signal, 1)
			signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
			select {
			case <-sigCh:
			case err := <-readErr:
				fmt.Fprintln(os.Stderr, "tun read error:", err)
			}

			fmt.Println("\nDisconnecting...")
			cancel()
			sender.Stop()
			return nil
		},
	}
	app.RunAndExitOnError()
}

// validateMtu rejects values that would silently truncate packets: the TUN read
// buffer is derived from the configured MTU, and the interface MTU is set from it.
func validateMtu(mtu int) error {
	if mtu < 576 || mtu > 9000 {
		return fmt.Errorf("invalid mtu %d: must be between 576 and 9000", mtu)
	}
	return nil
}

// describePacket formats a short one-line summary of an IP packet.
func describePacket(packet []byte) string {
	version := packet[0] >> 4
	switch version {
	case 4:
		if len(packet) < 20 {
			return fmt.Sprintf("IPv4 truncated len=%d", len(packet))
		}
		headerLen := int(packet[0]&0x0f) * 4
		src := net.IP(packet[12:16]).String()
		dst := net.IP(packet[16:20]).String()
		proto := fmt.Sprintf("PROTO%d", packet[9])
		switch packet[9] {
		case 6:
			proto = "TCP"
		case 17:
			proto = "UDP"
		case 1:
			proto = "ICMP"
		}
		if (packet[9] == 6 || packet[9] == 17) && headerLen >= 20 && len(packet) >= headerLen+4 {
			srcPort := binary.BigEndian.Uint16(packet[headerLen : headerLen+2])
			dstPort := binary.BigEndian.Uint16(packet[headerLen+2 : headerLen+4])
			return fmt.Sprintf("%s %s:%d->%s:%d (%d bytes)", proto, src, srcPort, dst, dstPort, len(packet))
		}
		return fmt.Sprintf("%s %s->%s (%d bytes)", proto, src, dst, len(packet))
	case 6:
		return fmt.Sprintf("IPv6 len=%d", len(packet))
	default:
		return fmt.Sprintf("ip_version=%d len=%d", version, len(packet))
	}
}

func humanBytes(n uint64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := uint64(unit), 0
	for v := n / unit; v >= unit; v /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}

// trafficStats holds counters shared between the TUN reader, the packet sender
// and the receive callback. All fields are updated with atomics so the stats
// ticker can read them without blocking the data path.
type trafficStats struct {
	txPackets  atomic.Uint64
	txBytes    atomic.Uint64
	txEnqueued atomic.Uint64
	txDropped  atomic.Uint64
	txSent     atomic.Uint64
	txFailed   atomic.Uint64
	rxPackets  atomic.Uint64
	rxBytes    atomic.Uint64
	rxWriteErr atomic.Uint64
}

func (s *trafficStats) line(queueLen, queueCap int) string {
	return fmt.Sprintf(
		"stats: tx %d pkt/%s rx %d pkt/%s dropped=%d sendFailed=%d writeErrors=%d queue=%d/%d",
		s.txPackets.Load(), humanBytes(s.txBytes.Load()),
		s.rxPackets.Load(), humanBytes(s.rxBytes.Load()),
		s.txDropped.Load(), s.txFailed.Load(), s.rxWriteErr.Load(),
		queueLen, queueCap,
	)
}

// packetSender decouples the TUN read loop from SendPacket: the reader never
// blocks on the network. Packets are sent by worker goroutines from a bounded
// queue; when the queue is full packets are dropped instead of stalling TUN.
type packetSender struct {
	sendFn  func(packet []byte, timeout time.Duration) bool
	timeout time.Duration
	queue   chan []byte
	stats   *trafficStats
	verbose bool
	stop    chan struct{}
	stopped atomic.Bool
	wg      sync.WaitGroup
}

func newPacketSender(workers, queueSize int, timeout time.Duration, stats *trafficStats, verbose bool, sendFn func([]byte, time.Duration) bool) *packetSender {
	if workers < 1 {
		workers = 1
	}
	if queueSize < 1 {
		queueSize = 1
	}
	s := &packetSender{
		sendFn:  sendFn,
		timeout: timeout,
		queue:   make(chan []byte, queueSize),
		stats:   stats,
		verbose: verbose,
		stop:    make(chan struct{}),
	}
	s.wg.Add(workers)
	for i := 0; i < workers; i++ {
		go func() {
			defer s.wg.Done()
			for {
				select {
				case packet := <-s.queue:
					if s.stopped.Load() {
						// shutting down: drop instead of blocking on the network
						s.stats.txDropped.Add(1)
						continue
					}
					s.deliver(packet)
				case <-s.stop:
					s.dropQueued()
					return
				}
			}
		}()
	}
	return s
}

func (s *packetSender) deliver(packet []byte) {
	if s.sendFn(packet, s.timeout) {
		s.stats.txSent.Add(1)
		return
	}
	s.stats.txFailed.Add(1)
	if s.verbose {
		fmt.Fprintln(os.Stderr, sendPacketFailureDiagnostic(packet))
	}
}

// dropQueued discards packets that were still buffered when Stop was called:
// graceful shutdown must not wait for the whole queue to drain over the network.
func (s *packetSender) dropQueued() {
	for {
		select {
		case <-s.queue:
			s.stats.txDropped.Add(1)
		default:
			return
		}
	}
}

// Enqueue never blocks. It returns false when the queue is full or the sender
// has stopped, and counts the packet as dropped so callers cannot forget the
// accounting.
func (s *packetSender) Enqueue(packet []byte) bool {
	if s.stopped.Load() {
		s.stats.txDropped.Add(1)
		return false
	}
	select {
	case s.queue <- packet:
		s.stats.txEnqueued.Add(1)
		return true
	default:
		s.stats.txDropped.Add(1)
		return false
	}
}

func (s *packetSender) queueLen() int {
	return len(s.queue)
}

// Stop asks the workers to finish, drops whatever is still queued and waits for
// the in-flight sends only. It is safe to call more than once.
func (s *packetSender) Stop() {
	if s.stopped.Swap(true) {
		return
	}
	close(s.stop)
	s.wg.Wait()
}

// stateFile is the JSON contract consumed by the GUI and scripts. It replaces
// scraping stdout for connection status.
type stateFile struct {
	Version       int       `json:"version"`
	Mode          string    `json:"mode"`
	Pid           int       `json:"pid"`
	Connected     bool      `json:"connected"`
	Provider      string    `json:"provider,omitempty"`
	TunName       string    `json:"tun_name,omitempty"`
	TunAddr       string    `json:"tun_addr,omitempty"`
	Mtu           int       `json:"mtu"`
	IPv6Blocked   bool      `json:"ipv6_blocked"`
	StartedAt     time.Time `json:"started_at"`
	TxPackets     uint64    `json:"tx_packets"`
	TxBytes       uint64    `json:"tx_bytes"`
	TxDropped     uint64    `json:"tx_dropped"`
	TxFailed      uint64    `json:"tx_failed"`
	RxPackets     uint64    `json:"rx_packets"`
	RxBytes       uint64    `json:"rx_bytes"`
	RxWriteErrors uint64    `json:"rx_write_errors"`
}

func writeStateFile(path string, st stateFile) error {
	if dir := filepath.Dir(path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			return err
		}
	}
	data, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// resolveStateFilePath keeps a root-run engine from writing (and later deleting)
// an arbitrary path supplied through the environment. The GUI sets
// URNETWORK_STATE_FILE for the user-level process; when the engine runs as root
// through sudo only the invoking user's config dir is trusted, and the default
// path is derived from SUDO_UID so state reporting keeps working even when the
// variable is not preserved by sudo.
func resolveStateFilePath(configured string) string {
	if os.Geteuid() != 0 {
		return configured
	}
	trusted := ""
	if uid := os.Getenv("SUDO_UID"); uid != "" {
		if u, err := user.LookupId(uid); err == nil && u.HomeDir != "" && u.HomeDir != "/root" {
			trusted = filepath.Join(u.HomeDir, ".config", "urnetwork")
		}
	}
	return resolveStateFilePathIn(configured, trusted)
}

// resolveStateFilePathIn is the pure part of resolveStateFilePath, split out so
// the trust rules can be tested without running as root.
func resolveStateFilePathIn(configured, trusted string) string {
	if trusted == "" {
		return ""
	}
	if configured == "" {
		return filepath.Join(trusted, "state.json")
	}
	abs, err := filepath.Abs(configured)
	if err != nil {
		return ""
	}
	rel, err := filepath.Rel(trusted, abs)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(os.PathSeparator)) {
		fmt.Fprintf(os.Stderr, "warning: ignoring state-file %q: outside %s\n", configured, trusted)
		return ""
	}
	return abs
}

func sendPacketFailureDiagnostic(packet []byte) string {
	const fallbackReason = "probable=tcp-collapse/backpressure/no-client"
	if len(packet) < 1 {
		return "[TUN!!] SendPacket FAILED len=0 probable=bad-packet"
	}

	version := packet[0] >> 4
	switch version {
	case 4:
		if len(packet) < 20 {
			return fmt.Sprintf("[TUN!!] SendPacket FAILED IPv4 len=%d probable=truncated-packet", len(packet))
		}
		srcIP := net.IP(packet[12:16]).String()
		dstIP := net.IP(packet[16:20]).String()
		proto := packet[9]
		switch proto {
		case 6:
			if len(packet) < 40 {
				return fmt.Sprintf("[TUN!!] SendPacket FAILED TCP %s->%s len=%d probable=truncated-tcp", srcIP, dstIP, len(packet))
			}
			srcPort := binary.BigEndian.Uint16(packet[20:22])
			dstPort := binary.BigEndian.Uint16(packet[22:24])
			return fmt.Sprintf("[TUN!!] SendPacket FAILED TCP %s:%d->%s:%d len=%d %s", srcIP, srcPort, dstIP, dstPort, len(packet), fallbackReason)
		case 17:
			if len(packet) < 28 {
				return fmt.Sprintf("[TUN!!] SendPacket FAILED UDP %s->%s len=%d probable=truncated-udp", srcIP, dstIP, len(packet))
			}
			srcPort := binary.BigEndian.Uint16(packet[20:22])
			dstPort := binary.BigEndian.Uint16(packet[22:24])
			return fmt.Sprintf("[TUN!!] SendPacket FAILED UDP %s:%d->%s:%d len=%d %s", srcIP, srcPort, dstIP, dstPort, len(packet), fallbackReason)
		default:
			return fmt.Sprintf("[TUN!!] SendPacket FAILED IPv4 proto=%d %s->%s len=%d probable=unsupported-protocol", proto, srcIP, dstIP, len(packet))
		}
	case 6:
		return fmt.Sprintf("[TUN!!] SendPacket FAILED IPv6 len=%d %s", len(packet), fallbackReason)
	default:
		return fmt.Sprintf("[TUN!!] SendPacket FAILED ip_version=%d len=%d probable=bad-tun-framing", version, len(packet))
	}
}

func vpnMultiClientSettings() *connect.MultiClientSettings {
	settings := connect.DefaultMultiClientSettings()
	settings.TcpCollapsePrevention = false
	return settings
}

func login(ctx context.Context, apiURL, userAuth, password string) (string, error) {
	api := connect.NewBringYourApi(ctx, connect.NewClientStrategyWithDefaults(ctx), apiURL)
	type loginResult struct {
		res *connect.AuthLoginWithPasswordResult
		err error
	}
	resChan := make(chan loginResult)
	api.AuthLoginWithPassword(
		&connect.AuthLoginWithPasswordArgs{UserAuth: userAuth, Password: password},
		connect.NewApiCallback(func(res *connect.AuthLoginWithPasswordResult, err error) {
			resChan <- loginResult{res, err}
		}),
	)
	res := <-resChan
	if res.err != nil {
		return "", res.err
	}
	if res.res.Error != nil {
		return "", errors.New(res.res.Error.Message)
	}
	if res.res.VerificationRequired != nil {
		return "", errors.New("verification required - please verify your account")
	}
	return res.res.Network.ByJwt, nil
}

func getProviderLocations(ctx context.Context, apiURL string, jwt string) (*FindLocationsResult, error) {
	strategy := connect.NewClientStrategyWithDefaults(ctx)
	return connect.HttpGetWithStrategy(
		ctx, strategy,
		fmt.Sprintf("%s/network/provider-locations", apiURL),
		jwt, &FindLocationsResult{},
		connect.NewNoopApiCallback[*FindLocationsResult](),
	)
}

// getProviderSpec resolves the requested location to a provider spec. It also
// returns a human-readable description of the selection for logs and state.
func getProviderSpec(locations *FindLocationsResult, city, country, region, providerID string) ([]*connect.ProviderSpec, string, error) {
	if providerID != "" {
		cid, err := connect.ParseId(providerID)
		if err != nil {
			return nil, "", fmt.Errorf("parse provider id failed: %w", err)
		}
		fmt.Println("provider match", cid)
		return []*connect.ProviderSpec{{ClientId: &cid}}, "provider " + cid.String(), nil
	}
	if city != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "city" && strings.EqualFold(v.Name, city) {
				fmt.Printf("city matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, fmt.Sprintf("%s (city)", v.Name), nil
			}
		}
	}
	if country != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "country" && strings.EqualFold(v.Name, country) {
				fmt.Printf("country matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, fmt.Sprintf("%s (country)", v.Name), nil
			}
		}
	}
	if region != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "region" && strings.EqualFold(v.Name, region) {
				fmt.Printf("region matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, fmt.Sprintf("%s (region)", v.Name), nil
			}
		}
	}
	best := lo.MaxBy(locations.Locations.Values(), func(a, b *LocationResult) bool {
		return a.ProviderCount > b.ProviderCount
	})
	if best != nil {
		fmt.Printf("auto-selected %q (%s), provider count %d\n", best.Name, best.LocationType, best.ProviderCount)
		return []*connect.ProviderSpec{{LocationId: best.LocationId}}, fmt.Sprintf("%s (%s)", best.Name, best.LocationType), nil
	}

	return nil, "", fmt.Errorf("no locations available")
}

func authNetworkClient(ctx context.Context, apiURL, jwt string, req *connect.AuthNetworkClientArgs) (string, error) {
	strategy := connect.NewClientStrategyWithDefaults(ctx)
	res, err := connect.HttpPostWithStrategy(
		ctx, strategy,
		fmt.Sprintf("%s/network/auth-client", apiURL),
		req, jwt,
		&connect.AuthNetworkClientResult{},
		connect.NewNoopApiCallback[*connect.AuthNetworkClientResult](),
	)
	if err != nil {
		return "", err
	}
	if res.Error != nil {
		return "", errors.New(res.Error.Message)
	}
	return res.ByClientJwt, nil
}

func parseByJwtClientId(byJwt string) (connect.Id, error) {
	claims := gojwt.MapClaims{}
	gojwt.NewParser().ParseUnverified(byJwt, claims)
	jwtClientId, ok := claims["client_id"]
	if !ok {
		return connect.Id{}, fmt.Errorf("byJwt does not contain claim client_id")
	}
	switch v := jwtClientId.(type) {
	case string:
		return connect.ParseId(v)
	default:
		return connect.Id{}, fmt.Errorf("byJwt has invalid type for client_id: %T", v)
	}
}

type systemTun struct {
	fd     int
	name   string
	prefix netip.Prefix
	mtu    int
	noPI   bool
	ctx    context.Context
	cancel context.CancelFunc
	mu     sync.Mutex
	closed bool
}

func tunExists(name string) bool {
	out, err := exec.Command("ip", "link", "show", name).CombinedOutput()
	return err == nil && len(out) > 0
}

func createTun(name string, prefix netip.Prefix, mtu int, replace bool) (*systemTun, error) {
	if tunExists(name) {
		if !replace {
			return nil, fmt.Errorf(
				"interface %q already exists: another VPN instance is probably running; stop it first (urnetwork-connect.sh off) or pass --replace-tun",
				name,
			)
		}
		exec.Command("ip", "link", "delete", name).Run()
	}

	fd, err := unix.Open("/dev/net/tun", unix.O_RDWR|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("open /dev/net/tun: %w", err)
	}

	var ifr [64]byte
	copy(ifr[:], name)
	// Try with IFF_NO_PI first (raw IP packets without 4-byte tun_pi header)
	binary.LittleEndian.PutUint16(ifr[16:18], uint16(unix.IFF_TUN|unix.IFF_NO_PI))

	if _, _, errno := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), uintptr(unix.TUNSETIFF), uintptr(unsafe.Pointer(&ifr[0]))); errno != 0 {
		unix.Close(fd)
		return nil, fmt.Errorf("TUNSETIFF: %w", errno)
	}

	actualName := strings.TrimRight(string(ifr[:16]), "\x00")

	ctx, cancel := context.WithCancel(context.Background())

	tun := &systemTun{
		fd:     fd,
		name:   actualName,
		prefix: prefix,
		mtu:    mtu,
		noPI:   true,
		ctx:    ctx,
		cancel: cancel,
	}

	return tun, nil
}

var ethPIP = []byte{0x08, 0x00}
var ethPIPv6 = []byte{0x86, 0xDD}

func hasTunPI(packet []byte) bool {
	return len(packet) >= 4 && packet[0] == 0x00 && packet[1] == 0x00 &&
		((packet[2] == 0x08 && packet[3] == 0x00) || (packet[2] == 0x86 && packet[3] == 0xDD))
}

func stripTunPI(packet []byte) []byte {
	if hasTunPI(packet) {
		return packet[4:]
	}
	return packet
}

func prependTunPI(packet []byte) []byte {
	if len(packet) == 0 {
		return packet
	}
	version := packet[0] >> 4
	pi := []byte{0x00, 0x00, 0x00, 0x00}
	if version == 6 {
		pi[2] = 0x86
		pi[3] = 0xDD
	} else {
		pi[2] = 0x08
		pi[3] = 0x00
	}
	result := make([]byte, len(pi)+len(packet))
	copy(result, pi)
	copy(result[4:], packet)
	return result
}

// Read reads one packet from the TUN device into buf and returns a
// packet-sized copy. Callers reuse buf across calls, so the returned slice must
// not alias buf: it may be queued for asynchronous sending.
func (t *systemTun) Read(buf []byte) ([]byte, error) {
	for {
		n, err := unix.Read(t.fd, buf)
		if err != nil {
			if err == unix.EAGAIN || err == unix.EINTR {
				continue
			}
			return nil, err
		}
		packet := make([]byte, n)
		copy(packet, buf[:n])
		if t.noPI {
			return packet, nil
		}
		return stripTunPI(packet), nil
	}
}

func (t *systemTun) Write(packet []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed {
		return 0, errors.New("tun closed")
	}
	if t.noPI {
		return unix.Write(t.fd, packet)
	}
	data := prependTunPI(packet)
	return unix.Write(t.fd, data)
}

func (t *systemTun) CloseTun() error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed {
		return nil
	}
	t.closed = true
	t.cancel()
	return unix.Close(t.fd)
}

func (t *systemTun) Name() string { return t.name }

func resolveBypassHosts(ctx context.Context) []string {
	hosts := []string{}
	for _, host := range []string{"api.bringyour.com", "connect.bringyour.com"} {
		addrs, err := net.DefaultResolver.LookupHost(ctx, host)
		if err == nil {
			for _, addr := range addrs {
				if ip := net.ParseIP(addr); ip != nil && ip.To4() == nil {
					hosts = append(hosts, addr+"/128")
				} else {
					hosts = append(hosts, addr+"/32")
				}
			}
		}
	}
	return hosts
}

func runCmd(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

// runCmdQuiet runs a command without forwarding stderr. Teardown is expected to
// race the kernel (the device may already be gone), so errors are not noise-worthy.
func runCmdQuiet(name string, args ...string) error {
	return exec.Command(name, args...).Run()
}

func ipv6BlackholeArgs() []string {
	return []string{"-6", "route", "add", "blackhole", "default", "metric", "10"}
}

func ipv6BlackholeDelArgs() []string {
	return []string{"-6", "route", "del", "blackhole", "default", "metric", "10"}
}

func setupRouting(iface string, prefix netip.Prefix, bypassCIDRs []string, mtu int, blockIPv6 bool) (addedBypasses []string, err error) {
	defaultGw, err := getDefaultGateway()
	if err != nil {
		return nil, fmt.Errorf("get default gateway: %w", err)
	}

	addedBypasses = selectBypassCIDRs(defaultGw, bypassCIDRs)

	for _, cidr := range addedBypasses {
		if err := runCmd("ip", "route", "replace", cidr, "via", defaultGw.String()); err != nil {
			fmt.Fprintf(os.Stderr, "warning: bypass route %s: %v\n", cidr, err)
		}
	}

	if err := runCmd("ip", "addr", "replace", prefix.String(), "dev", iface); err != nil {
		return addedBypasses, fmt.Errorf("add addr: %w", err)
	}
	if err := runCmd("ip", "link", "set", iface, "mtu", fmt.Sprintf("%d", mtu), "up"); err != nil {
		return addedBypasses, fmt.Errorf("link up: %w", err)
	}
	if err := runCmd("ip", defaultRouteArgs(iface, prefix)...); err != nil {
		return addedBypasses, fmt.Errorf("add default route: %w", err)
	}

	if blockIPv6 {
		if err := runCmd("ip", ipv6BlackholeArgs()...); err != nil {
			fmt.Fprintf(os.Stderr, "warning: could not block IPv6, traffic may leak outside the tunnel: %v\n", err)
		}
	}

	return addedBypasses, nil
}

func defaultRouteArgs(iface string, prefix netip.Prefix) []string {
	return []string{"route", "replace", "default", "dev", iface, "src", prefix.Addr().String(), "metric", "10"}
}

func selectBypassCIDRs(defaultGw netip.Addr, bypassCIDRs []string) []string {
	bypassCIDRs = lo.Uniq(bypassCIDRs)
	selected := make([]string, 0, len(bypassCIDRs))
	for _, cidr := range bypassCIDRs {
		prefix, err := netip.ParsePrefix(cidr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "warning: bypass route %s: %v\n", cidr, err)
			continue
		}
		if prefix.Addr().Is4() != defaultGw.Is4() {
			fmt.Fprintf(os.Stderr, "warning: skipping bypass route %s for gateway %s\n", cidr, defaultGw.String())
			continue
		}
		selected = append(selected, cidr)
	}
	return selected
}

// teardownRouting reverses setupRouting. It is best effort and idempotent: the
// interface or routes may already be gone (crash, manual cleanup, another
// instance), which must not produce scary errors on a normal disconnect.
func teardownRouting(iface string, prefix netip.Prefix, bypassCIDRs []string, blockIPv6 bool) {
	if blockIPv6 {
		runCmdQuiet("ip", ipv6BlackholeDelArgs()...)
	}
	runCmdQuiet("ip", "route", "del", "default", "dev", iface, "metric", "10")
	for _, cidr := range lo.Uniq(bypassCIDRs) {
		runCmdQuiet("ip", "route", "del", cidr)
	}
	runCmdQuiet("ip", "addr", "del", prefix.String(), "dev", iface)
	runCmdQuiet("ip", "link", "set", iface, "down")
}

func getDefaultGateway() (netip.Addr, error) {
	data, err := os.ReadFile("/proc/net/route")
	if err != nil {
		return netip.Addr{}, err
	}
	for _, line := range strings.Split(string(data), "\n")[1:] {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		if fields[1] == "00000000" && fields[3] == "0003" {
			dst := fields[2]
			ipBytes := make([]byte, 4)
			for i := 0; i < 4; i++ {
				val := uint32(0)
				fmt.Sscanf(dst[i*2:i*2+2], "%x", &val)
				ipBytes[3-i] = byte(val)
			}
			return netip.AddrFrom4(*(*[4]byte)(ipBytes)), nil
		}
	}
	return netip.Addr{}, errors.New("no default gateway found")
}
