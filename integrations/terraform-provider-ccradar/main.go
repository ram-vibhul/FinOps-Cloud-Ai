// Command terraform-provider-ccradar is the CloudCostRadar Terraform provider (11.23).
package main

import (
	"context"
	"flag"
	"log"

	"github.com/hashicorp/terraform-plugin-framework/providerserver"

	"cloudcostradar/terraform-provider-ccradar/internal/provider"
)

// version is set at build time with -ldflags "-X main.version=1.2.3".
var version = "dev"

func main() {
	var debug bool
	flag.BoolVar(&debug, "debug", false, "run the provider with support for debuggers")
	flag.Parse()
	err := providerserver.Serve(context.Background(), provider.New(version), providerserver.ServeOpts{
		Address: "registry.terraform.io/cloudcostradar/ccradar",
		Debug:   debug,
	})
	if err != nil {
		log.Fatal(err)
	}
}
