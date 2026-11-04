package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	// "net/netip"
	"os"
	"strings"
	"time"

	gojwt "github.com/golang-jwt/jwt/v5"
	"github.com/samber/lo"
	"github.com/things-go/go-socks5"
	"github.com/urfave/cli/v2"
	"github.com/urnetwork/connect"
	"github.com/urnetwork/connect/protocol"
	"github.com/urnetwork/proxy"
)

// this value is set via the linker, e.g.
// -ldflags "-X main.Version=$WARP_VERSION-$WARP_VERSION_CODE"
var Version string

func init() {
	initGlog()
}

func initGlog() {
	// flag.Set("logtostderr", "true")
	flag.Set("stderrthreshold", "WARNING")
	flag.Set("v", "0")
	// unlike unix, the android/ios standard is for diagnostics to go to stdout
	os.Stderr = os.Stdout
}

func main() {
	cfg := struct {
		addr        string
		apiURL      string
		platformURL string
		userAuth    string
		password    string
		providerID  string
		city        string
		country     string
		region      string
		verbose     bool
	}{}
	app := &cli.App{
		Flags: []cli.Flag{
			&cli.StringFlag{
				Name:        "addr",
				Usage:       "Socks5 server address",
				EnvVars:     []string{"ADDR"},
				Destination: &cfg.addr,
				Value:       "127.0.0.1:9999",
			},
			&cli.StringFlag{
				Name:        "api-url",
				Usage:       "API URL",
				EnvVars:     []string{"API_URL"},
				Destination: &cfg.apiURL,
				Value:       "https://api.bringyour.com",
			},
			&cli.StringFlag{
				Name:        "platform-url",
				Usage:       "Platform URL",
				EnvVars:     []string{"PLATFORM_URL"},
				Destination: &cfg.platformURL,
				Value:       "wss://connect.bringyour.com",
			},
			&cli.StringFlag{
				Name:        "user-auth",
				Usage:       "User auth",
				EnvVars:     []string{"USER_AUTH"},
				Destination: &cfg.userAuth,
				Required:    true,
			},
			&cli.StringFlag{
				Name:        "password",
				Usage:       "Password",
				EnvVars:     []string{"PASSWORD"},
				Destination: &cfg.password,
				Required:    true,
			},
			&cli.StringFlag{
				Name:        "provider-id",
				Usage:       "Provider ID",
				EnvVars:     []string{"PROVIDER_ID"},
				Destination: &cfg.providerID,
			},
			&cli.StringFlag{
				Name:        "city",
				Usage:       "City",
				EnvVars:     []string{"CITY"},
				Destination: &cfg.city,
			},
			&cli.StringFlag{
				Name:        "country",
				Usage:       "Country",
				EnvVars:     []string{"COUNTRY"},
				Destination: &cfg.country,
			},
			&cli.StringFlag{
				Name:        "region",
				Usage:       "Region",
				EnvVars:     []string{"REGION"},
				Destination: &cfg.region,
			},
			&cli.BoolFlag{
				Name:        "verbose",
				Usage:       "Log every proxied connection",
				EnvVars:     []string{"URNETWORK_VERBOSE"},
				Destination: &cfg.verbose,
			},
		},
		Name: "socksproxy",
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
					fmt.Print(formatLocationList(locations))
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

			locations, err := getProviderLocations(
				ctx,
				cfg.apiURL,
				jwt,
			)
			if err != nil {
				return fmt.Errorf("get locations failed: %w", err)
			}

			providersSpec, err := getProviderSpec(
				locations,
				cfg.city,
				cfg.country,
				cfg.region,
				cfg.providerID,
			)
			if err != nil {
				return fmt.Errorf("get provider spec failed: %w", err)
			}

			clientJWT, err := authNetworkClient(
				ctx,
				cfg.apiURL,
				jwt,
				&connect.AuthNetworkClientArgs{
					Description: "my device",
					DeviceSpec:  "socks5",
				},
			)

			if err != nil {
				return fmt.Errorf("auth network client failed: %w", err)
			}

			clientID, err := parseByJwtClientId(clientJWT)
			if err != nil {
				return fmt.Errorf("parse byJwt client id failed: %w", err)
			}

			fmt.Println("my clientID:", clientID)

			generator := connect.NewApiMultiClientGenerator(
				ctx,
				providersSpec,
				connect.NewClientStrategyWithDefaults(ctx),
				// exclude self
				[]connect.Id{
					clientID,
				},
				cfg.apiURL,
				clientJWT,
				cfg.platformURL,
				"my device",
				"socks5",
				"0.0.0",
				&clientID,
				// connect.DefaultClientSettingsNoNetworkEvents,
				connect.DefaultClientSettings,
				connect.DefaultApiMultiClientGeneratorSettings(),
			)

			dev, err := proxy.CreateTunWithDefaults(ctx)
			if err != nil {
				return fmt.Errorf("create net tun failed: %w", err)
			}

			mc := connect.NewRemoteUserNatMultiClientWithDefaults(
				ctx,
				generator,
				func(source connect.TransferPath, provideMode protocol.ProvideMode, ipPath *connect.IpPath, packet []byte) {
					_, err := dev.Write(packet)
					if err != nil {
						fmt.Println("packet write error:", err)
					}
				},
				protocol.ProvideMode_Network,
			)

			source := connect.SourceId(clientID)

			go func() {
				for {
					packet, err := dev.Read()
					if err != nil {
						fmt.Println("read error:", err)
						return
					}
					if len(packet) == 0 {
						continue
					}
					mc.SendPacket(
						source,
						protocol.ProvideMode_Network,
						packet,
						time.Second*15,
					)
				}
			}()

			server := socks5.NewServer(
				socks5.WithLogger(socks5.NewLogger(log.New(os.Stdout, "socks5: ", log.LstdFlags))),
				socks5.WithDialAndRequest(func(ctx context.Context, network, addr string, request *socks5.Request) (net.Conn, error) {

					if cfg.verbose {
						fmt.Println("Dialing", network, addr, request.RawDestAddr.FQDN)
					}

					// ap, err := netip.ParseAddrPort(addr)
					// if err != nil {
					// 	return nil, err
					// }

					return dev.DialContext(ctx, "tcp", addr)
				}),
			)

			if host, _, err := net.SplitHostPort(cfg.addr); err == nil {
				if host == "" || host == "0.0.0.0" || host == "::" {
					fmt.Fprintf(os.Stderr,
						"warning: socks5 listens on all interfaces (%s); prefer 127.0.0.1:9999 unless you need remote access\n",
						cfg.addr)
				}
			}

			listener, err := net.Listen("tcp", cfg.addr)
			if err != nil {
				return fmt.Errorf("socks5 listen on %s failed: %w", cfg.addr, err)
			}
			go func() {
				if err := server.Serve(listener); err != nil && !errors.Is(err, net.ErrClosed) {
					fmt.Fprintln(os.Stderr, "socks5 server error:", err)
				}
			}()

			fmt.Printf("socks5 server is listening on %s\n", cfg.addr)

			<-ctx.Done()

			return nil

		},
	}
	app.RunAndExitOnError()

}

func formatLocationList(locations *FindLocationsResult) string {
	var out strings.Builder
	countries := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "country" })
	regions := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "region" })
	cities := lo.Filter(locations.Locations.Values(), func(v *LocationResult, _ int) bool { return v.LocationType == "city" })
	out.WriteString("countries:\n")
	for _, c := range countries {
		fmt.Fprintf(&out, "  %s\n", c.Name)
	}
	out.WriteString("regions:\n")
	for _, r := range regions {
		fmt.Fprintf(&out, "  %s\n", r.Name)
	}
	out.WriteString("cities:\n")
	for _, c := range cities {
		fmt.Fprintf(&out, "  %s\n", c.Name)
	}
	return out.String()
}

func getProviderSpec(
	locations *FindLocationsResult,
	city string,
	country string,
	region string,
	providerID string,
) ([]*connect.ProviderSpec, error) {

	if providerID != "" {
		cid, err := connect.ParseId(providerID)
		if err != nil {
			return nil, fmt.Errorf("parse provider id failed: %w", err)
		}

		fmt.Println("provider match", cid)

		return []*connect.ProviderSpec{
			{
				ClientId: &cid,
			},
		}, nil
	}

	if city != "" {
		for _, v := range locations.Locations.Values() {

			switch v.LocationType {
			case "city":
				if strings.ToLower(v.Name) == strings.ToLower(city) {
					fmt.Printf("city matched %q, provider count %d\n", v.Name, v.ProviderCount)
					return []*connect.ProviderSpec{
						{
							LocationId: v.LocationId,
						},
					}, nil
				}
			}

		}
	}

	if country != "" {

		for _, v := range locations.Locations.Values() {

			switch v.LocationType {
			case "country":
				if strings.ToLower(v.Name) == strings.ToLower(country) {
					fmt.Printf("country matched %q, provider count %d\n", v.Name, v.ProviderCount)
					return []*connect.ProviderSpec{
						{
							LocationId: v.LocationId,
						},
					}, nil
				}
			}

		}
	}

	if region != "" {

		for _, v := range locations.Locations.Values() {

			switch v.LocationType {
			case "region":
				if strings.ToLower(v.Name) == strings.ToLower(region) {
					fmt.Printf("region matched %q, provider count %d\n", v.Name, v.ProviderCount)
					return []*connect.ProviderSpec{
						{
							LocationId: v.LocationId,
						},
					}, nil
				}
			}

		}
	}

	best := lo.MaxBy(locations.Locations.Values(), func(a, b *LocationResult) bool {
		return a.ProviderCount > b.ProviderCount
	})
	if best != nil {
		fmt.Printf("auto-selected %q (%s), provider count %d\n", best.Name, best.LocationType, best.ProviderCount)
		return []*connect.ProviderSpec{
			{
				LocationId: best.LocationId,
			},
		}, nil
	}

	return nil, fmt.Errorf("no locations available")

}

func login(ctx context.Context, apiURL, userAuth, password string) (string, error) {
	api := connect.NewBringYourApi(
		ctx,
		connect.NewClientStrategyWithDefaults(ctx),
		apiURL,
	)

	// api.AuthNetworkClient()
	type loginResult struct {
		res *connect.AuthLoginWithPasswordResult
		err error
	}

	resChan := make(chan loginResult)

	api.AuthLoginWithPassword(
		&connect.AuthLoginWithPasswordArgs{
			UserAuth: userAuth,
			Password: password,
		},
		connect.NewApiCallback(
			func(res *connect.AuthLoginWithPasswordResult, err error) {
				resChan <- loginResult{res, err}
			},
		),
	)

	res := <-resChan
	if res.err != nil {
		return "", fmt.Errorf("api error: %w", res.err)
	}
	if res.res == nil {
		return "", errors.New("api response is empty")
	}
	if res.res.Error != nil {
		return "", errors.New(res.res.Error.Message)
	}

	if res.res.VerificationRequired != nil {
		return "", errors.New("verification required")
	}

	return res.res.Network.ByJwt, nil

}

func getProviderLocations(ctx context.Context, apiURL string, jwt string) (*FindLocationsResult, error) {

	strategy := connect.NewClientStrategyWithDefaults(ctx)

	return connect.HttpGetWithStrategy(
		ctx,
		strategy,
		fmt.Sprintf("%s/network/provider-locations", apiURL),
		jwt,
		&FindLocationsResult{},
		connect.NewNoopApiCallback[*FindLocationsResult](),
	)

}

// func (self *BringYourApi) FindProviders(findProviders *FindProvidersArgs, callback FindProvidersCallback) {
// 	go connect.HandleError(func() {
// 		connect.HttpPostWithStrategy(
// 			self.ctx,
// 			self.clientStrategy,
// 			fmt.Sprintf("%s/network/find-providers", self.apiUrl),
// 			findProviders,
// 			self.GetByJwt(),
// 			&FindProvidersResult{},
// 			callback,
// 		)
// 	})
// }

func findProviders(ctx context.Context, apiURL string, jwt string, args *FindProvidersArgs) (*FindProvidersResult, error) {
	strategy := connect.NewClientStrategyWithDefaults(ctx)

	return connect.HttpPostWithStrategy(
		ctx,
		strategy,
		fmt.Sprintf("%s/network/find-providers", apiURL),
		args,
		jwt,
		&FindProvidersResult{},
		connect.NewNoopApiCallback[*FindProvidersResult](),
	)
}

func authNetworkClient(ctx context.Context, apiURL, jwt string, req *connect.AuthNetworkClientArgs) (string, error) {
	strategy := connect.NewClientStrategyWithDefaults(ctx)

	res, err := connect.HttpPostWithStrategy(
		ctx,
		strategy,
		fmt.Sprintf("%s/network/auth-client", apiURL),
		req,
		jwt,
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
		return connect.Id{}, fmt.Errorf("byJwt hav invalid type for client_id: %T", v)
	}
}
