package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/netip"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"sync"
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
			ctx := c.Context

			jwt, err := login(ctx, cfg.apiURL, cfg.userAuth, cfg.password)
			if err != nil {
				return fmt.Errorf("login failed: %w", err)
			}

			locations, err := getProviderLocations(ctx, cfg.apiURL, jwt)
			if err != nil {
				return fmt.Errorf("get locations failed: %w", err)
			}

			providersSpec, err := getProviderSpec(locations, cfg.city, cfg.country, cfg.region, cfg.providerID)
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

			tun, err := createTun(cfg.tunName, tunPrefix, cfg.mtu)
			if err != nil {
				return fmt.Errorf("create tun failed: %w", err)
			}
			defer tun.CloseTun()

			if !cfg.noRoute {
				bypassCIDRs := cfg.bypass.Value()
				bypassCIDRs = append(bypassCIDRs, resolveBypassHosts(ctx)...)
				addedBypasses, err := setupRouting(cfg.tunName, tunPrefix, bypassCIDRs)
				if err != nil {
					tun.CloseTun()
					return fmt.Errorf("setup routing failed: %w", err)
				}
				defer teardownRouting(cfg.tunName, tunPrefix, addedBypasses)
			}

			source := connect.SourceId(clientID)

			mc := connect.NewRemoteUserNatMultiClientWithDefaults(
				ctx,
				generator,
				func(source connect.TransferPath, provideMode protocol.ProvideMode, ipPath *connect.IpPath, packet []byte) {
					if len(packet) > 0 {
						srcIP := ""
						dstIP := ""
						proto := ""
						version := packet[0] >> 4
						if version == 4 && len(packet) >= 20 {
							srcIP = net.IP(packet[12:16]).String()
							dstIP = net.IP(packet[16:20]).String()
							switch packet[9] {
							case 6:
								proto = "TCP"
							case 17:
								proto = "UDP"
							case 1:
								proto = "ICMP"
							default:
								proto = fmt.Sprintf("PROTO%d", packet[9])
							}
						}
						fmt.Printf("[<<TUN] %s %s->%s (%d bytes)\n", proto, srcIP, dstIP, len(packet))
					}
					_, err := tun.Write(packet)
					if err != nil {
						fmt.Fprintln(os.Stderr, "tun write error:", err)
					}
				},
				protocol.ProvideMode_Network,
			)

			go func() {
				for {
					packet, err := tun.Read()
					if err != nil {
						fmt.Fprintln(os.Stderr, "tun read error:", err)
						return
					}
					if len(packet) > 0 {
						srcIP := ""
						dstIP := ""
						proto := ""
						version := packet[0] >> 4
						if version == 4 && len(packet) >= 20 {
							srcIP = net.IP(packet[12:16]).String()
							dstIP = net.IP(packet[16:20]).String()
							switch packet[9] {
							case 6:
								proto = "TCP"
							case 17:
								proto = "UDP"
							case 1:
								proto = "ICMP"
							default:
								proto = fmt.Sprintf("PROTO%d", packet[9])
							}
						}
						fmt.Printf("[TUN>>] %s %s->%s (%d bytes)\n", proto, srcIP, dstIP, len(packet))
					}
					ok := mc.SendPacket(
						source,
						protocol.ProvideMode_Network,
						packet,
						time.Second*15,
					)
					if !ok {
						fmt.Fprintf(os.Stderr, "[TUN!!] SendPacket FAILED\n")
					}
				}
			}()

			fmt.Printf("VPN connected. TUN: %s, IP: %s\n", cfg.tunName, tunPrefix)
			fmt.Println("Press Ctrl+C to disconnect.")

			sigCh := make(chan os.Signal, 1)
			signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
			<-sigCh

			fmt.Println("\nDisconnecting...")
			return nil
		},
	}
	app.RunAndExitOnError()
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

func getProviderSpec(locations *FindLocationsResult, city, country, region, providerID string) ([]*connect.ProviderSpec, error) {
	if providerID != "" {
		cid, err := connect.ParseId(providerID)
		if err != nil {
			return nil, fmt.Errorf("parse provider id failed: %w", err)
		}
		fmt.Println("provider match", cid)
		return []*connect.ProviderSpec{{ClientId: &cid}}, nil
	}
	if city != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "city" && strings.EqualFold(v.Name, city) {
				fmt.Printf("city matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, nil
			}
		}
	}
	if country != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "country" && strings.EqualFold(v.Name, country) {
				fmt.Printf("country matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, nil
			}
		}
	}
	if region != "" {
		for _, v := range locations.Locations.Values() {
			if v.LocationType == "region" && strings.EqualFold(v.Name, region) {
				fmt.Printf("region matched %q, provider count %d\n", v.Name, v.ProviderCount)
				return []*connect.ProviderSpec{{LocationId: v.LocationId}}, nil
			}
		}
	}
	best := lo.MaxBy(locations.Locations.Values(), func(a, b *LocationResult) bool {
		return a.ProviderCount > b.ProviderCount
	})
	if best != nil {
		fmt.Printf("auto-selected %q (%s), provider count %d\n", best.Name, best.LocationType, best.ProviderCount)
		return []*connect.ProviderSpec{{LocationId: best.LocationId}}, nil
	}

	return nil, fmt.Errorf("no locations available")
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

func createTun(name string, prefix netip.Prefix, mtu int) (*systemTun, error) {
	// Delete existing TUN device to avoid stale flags
	exec.Command("ip", "link", "delete", name).Run()
	exec.Command("ip", "tuntap", "del", name, "mode", "tun").Run()

	fd, err := unix.Open("/dev/net/tun", unix.O_RDWR|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, fmt.Errorf("open /dev/net/tun: %w", err)
	}

	var ifr [64]byte
	copy(ifr[:], name)
	// Try with IFF_NO_PI first (raw IP packets without 4-byte tun_pi header)
	ifr[16] = 0x01 | 0x10 // IFF_TUN | IFF_NO_PI
	ifr[17] = 0x00

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

func (t *systemTun) Read() ([]byte, error) {
	buf := make([]byte, t.mtu+64)
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

func setupRouting(iface string, prefix netip.Prefix, bypassCIDRs []string) ([]string, error) {
	defaultGw, err := getDefaultGateway()
	if err != nil {
		return nil, fmt.Errorf("get default gateway: %w", err)
	}

	addedBypasses := selectBypassCIDRs(defaultGw, bypassCIDRs)

	for _, cidr := range addedBypasses {
		if err := runCmd("ip", "route", "replace", cidr, "via", defaultGw.String()); err != nil {
			fmt.Fprintf(os.Stderr, "warning: bypass route %s: %v\n", cidr, err)
		}
	}

	if err := runCmd("ip", "addr", "replace", prefix.String(), "dev", iface); err != nil {
		return nil, fmt.Errorf("add addr: %w", err)
	}
	if err := runCmd("ip", "link", "set", iface, "mtu", "1400", "up"); err != nil {
		return nil, fmt.Errorf("link up: %w", err)
	}
	if err := runCmd("ip", "route", "replace", "default", "dev", iface, "metric", "10"); err != nil {
		return nil, fmt.Errorf("add default route: %w", err)
	}

	return addedBypasses, nil
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

func teardownRouting(iface string, prefix netip.Prefix, bypassCIDRs []string) {
	runCmd("ip", "route", "del", "default", "dev", iface, "metric", "10")
	for _, cidr := range lo.Uniq(bypassCIDRs) {
		runCmd("ip", "route", "del", cidr)
	}
	runCmd("ip", "addr", "del", prefix.String(), "dev", iface)
	runCmd("ip", "link", "set", iface, "down")
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
