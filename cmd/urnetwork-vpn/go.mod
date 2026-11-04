module github.com/user/urnetwork-vpn

go 1.26

require (
	github.com/golang-jwt/jwt/v5 v5.3.1
	github.com/samber/lo v1.50.0
	github.com/urfave/cli/v2 v2.27.7
	github.com/urnetwork/connect v0.0.0
	github.com/urnetwork/userwireguard v0.0.0-00010101000000-000000000000
	golang.org/x/net v0.53.0
	golang.org/x/sys v0.43.0
)

require (
	github.com/cpuguy83/go-md2man/v2 v2.0.7 // indirect
	github.com/google/gopacket v1.1.19 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/gorilla/websocket v1.5.3 // indirect
	github.com/oklog/ulid/v2 v2.1.1 // indirect
	github.com/pion/datachannel v1.6.0 // indirect
	github.com/pion/dtls/v3 v3.1.2 // indirect
	github.com/pion/ice/v4 v4.2.5 // indirect
	github.com/pion/interceptor v0.1.45 // indirect
	github.com/pion/logging v0.2.4 // indirect
	github.com/pion/mdns/v2 v2.1.0 // indirect
	github.com/pion/randutil v0.1.0 // indirect
	github.com/pion/rtcp v1.2.16 // indirect
	github.com/pion/rtp v1.10.2 // indirect
	github.com/pion/sctp v1.9.5 // indirect
	github.com/pion/sdp/v3 v3.0.18 // indirect
	github.com/pion/srtp/v3 v3.0.10 // indirect
	github.com/pion/stun/v3 v3.1.2 // indirect
	github.com/pion/transport/v4 v4.0.1 // indirect
	github.com/pion/turn/v5 v5.0.3 // indirect
	github.com/pion/webrtc/v4 v4.2.12 // indirect
	github.com/quic-go/quic-go v0.59.0 // indirect
	github.com/russross/blackfriday/v2 v2.1.0 // indirect
	github.com/urnetwork/glog v0.0.0 // indirect
	github.com/wlynxg/anet v0.0.5 // indirect
	github.com/xrash/smetrics v0.0.0-20250705151800-55b8f293f342 // indirect
	golang.org/x/crypto v0.50.0 // indirect
	golang.org/x/exp v0.0.0-20260410095643-746e56fc9e2f // indirect
	golang.org/x/text v0.36.0 // indirect
	golang.org/x/time v0.15.0 // indirect
	google.golang.org/protobuf v1.36.11 // indirect
	src.agwa.name/tlshacks v0.0.2 // indirect
)

replace github.com/urnetwork/connect => ../../lib/connect

replace github.com/urnetwork/proxy => ../../lib/proxy

replace github.com/urnetwork/glog => ../../lib/glog

replace github.com/urnetwork/userwireguard => ../../lib/userwireguard
