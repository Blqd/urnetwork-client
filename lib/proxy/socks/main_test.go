package main

import (
	"strings"
	"testing"

	"github.com/urnetwork/connect"
)

func testId(value byte) *connect.Id {
	id := connect.Id{}
	id[15] = value
	return &id
}

func testLocations(values ...*LocationResult) *FindLocationsResult {
	return &FindLocationsResult{
		Locations: &LocationResultList{
			exportedList: exportedList[*LocationResult]{
				values: values,
			},
		},
	}
}

func TestGetProviderSpecMatchesExplicitLocation(t *testing.T) {
	cityId := testId(1)
	countryId := testId(2)
	regionId := testId(3)
	locations := testLocations(
		&LocationResult{LocationId: cityId, LocationType: "city", Name: "Amsterdam", ProviderCount: 10},
		&LocationResult{LocationId: countryId, LocationType: "country", Name: "Netherlands", ProviderCount: 20},
		&LocationResult{LocationId: regionId, LocationType: "region", Name: "Europe", ProviderCount: 30},
	)

	specs, err := getProviderSpec(locations, "amsterdam", "", "", "")
	if err != nil {
		t.Fatalf("getProviderSpec city: %v", err)
	}
	if specs[0].LocationId != cityId {
		t.Fatalf("city LocationId = %v, want %v", specs[0].LocationId, cityId)
	}

	specs, err = getProviderSpec(locations, "", "netherlands", "", "")
	if err != nil {
		t.Fatalf("getProviderSpec country: %v", err)
	}
	if specs[0].LocationId != countryId {
		t.Fatalf("country LocationId = %v, want %v", specs[0].LocationId, countryId)
	}

	specs, err = getProviderSpec(locations, "", "", "europe", "")
	if err != nil {
		t.Fatalf("getProviderSpec region: %v", err)
	}
	if specs[0].LocationId != regionId {
		t.Fatalf("region LocationId = %v, want %v", specs[0].LocationId, regionId)
	}
}

func TestGetProviderSpecAutoSelectsLargestProviderCount(t *testing.T) {
	smallId := testId(1)
	largeId := testId(2)
	locations := testLocations(
		&LocationResult{LocationId: smallId, LocationType: "country", Name: "Small", ProviderCount: 2},
		&LocationResult{LocationId: largeId, LocationType: "region", Name: "Large", ProviderCount: 20},
	)

	specs, err := getProviderSpec(locations, "", "", "", "")
	if err != nil {
		t.Fatalf("getProviderSpec auto-select: %v", err)
	}
	if specs[0].LocationId != largeId {
		t.Fatalf("auto-selected LocationId = %v, want %v", specs[0].LocationId, largeId)
	}
}

func TestGetProviderSpecProviderId(t *testing.T) {
	providerId := connect.NewId()

	specs, err := getProviderSpec(testLocations(), "", "", "", providerId.String())
	if err != nil {
		t.Fatalf("getProviderSpec provider id: %v", err)
	}
	if specs[0].ClientId == nil || *specs[0].ClientId != providerId {
		t.Fatalf("ClientId = %v, want %v", specs[0].ClientId, providerId)
	}
}

func TestFormatLocationList(t *testing.T) {
	out := formatLocationList(testLocations(
		&LocationResult{LocationType: "city", Name: "Amsterdam"},
		&LocationResult{LocationType: "country", Name: "Netherlands"},
		&LocationResult{LocationType: "region", Name: "Europe"},
	))

	for _, want := range []string{
		"countries:\n  Netherlands",
		"regions:\n  Europe",
		"cities:\n  Amsterdam",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("formatLocationList missing %q in:\n%s", want, out)
		}
	}
}
