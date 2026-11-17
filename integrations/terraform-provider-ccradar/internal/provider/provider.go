// Package provider implements the CloudCostRadar Terraform provider: budgets, policies
// and virtual tags as code, through the public REST API and an organisation API key.
package provider

import (
	"context"
	"os"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type ccradarProvider struct{ version string }

type providerModel struct {
	Endpoint types.String `tfsdk:"endpoint"`
	APIKey   types.String `tfsdk:"api_key"`
}

// New returns the provider factory.
func New(version string) func() provider.Provider {
	return func() provider.Provider { return &ccradarProvider{version: version} }
}

func (p *ccradarProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "ccradar"
	resp.Version = p.version
}

func (p *ccradarProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manage CloudCostRadar budgets, policies and virtual tags as code.",
		Attributes: map[string]schema.Attribute{
			"endpoint": schema.StringAttribute{
				Optional:    true,
				Description: "Your CloudCostRadar address, e.g. https://ccradar.example.com. Or set CCRADAR_ENDPOINT.",
			},
			"api_key": schema.StringAttribute{
				Optional:    true,
				Sensitive:   true,
				Description: "An organisation API key with the admin scope (clk_...). Or set CCRADAR_API_KEY.",
			},
		},
	}
}

func (p *ccradarProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	endpoint, key := os.Getenv("CCRADAR_ENDPOINT"), os.Getenv("CCRADAR_API_KEY")
	if !cfg.Endpoint.IsNull() && !cfg.Endpoint.IsUnknown() {
		endpoint = cfg.Endpoint.ValueString()
	}
	if !cfg.APIKey.IsNull() && !cfg.APIKey.IsUnknown() {
		key = cfg.APIKey.ValueString()
	}
	if endpoint == "" || key == "" {
		resp.Diagnostics.AddError("Missing configuration", "Set endpoint and api_key (or CCRADAR_ENDPOINT and CCRADAR_API_KEY).")
		return
	}
	client, err := NewClient(endpoint, key, p.version)
	if err != nil {
		resp.Diagnostics.AddError("Invalid configuration", err.Error())
		return
	}
	resp.ResourceData = client
	resp.DataSourceData = client
}

func (p *ccradarProvider) Resources(context.Context) []func() resource.Resource {
	return []func() resource.Resource{NewBudgetResource, NewPolicyResource, NewVirtualTagResource}
}

func (p *ccradarProvider) DataSources(context.Context) []func() datasource.DataSource {
	return nil
}

// clientFrom is shared by the resources' Configure methods.
func clientFrom(data any, resp *resource.ConfigureResponse) *Client {
	if data == nil {
		return nil // not configured yet (validation runs before Configure)
	}
	c, ok := data.(*Client)
	if !ok {
		resp.Diagnostics.AddError("Unexpected provider data", "please report this issue")
		return nil
	}
	return c
}
