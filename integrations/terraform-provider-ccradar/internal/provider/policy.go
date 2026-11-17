package provider

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type policyResource struct{ client *Client }

type policyModel struct {
	ID            types.String `tfsdk:"id"`
	Name          types.String `tfsdk:"name"`
	Kind          types.String `tfsdk:"kind"`
	ParamsJSON    types.String `tfsdk:"params_json"`
	ResourceTypes types.List   `tfsdk:"resource_types"`
	Severity      types.String `tfsdk:"severity"`
	Enabled       types.Bool   `tfsdk:"enabled"`
}

// NewPolicyResource is the ccradar_policy resource.
func NewPolicyResource() resource.Resource { return &policyResource{} }

func (r *policyResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_policy"
}

func (r *policyResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A governance policy: checked against the inventory, Terraform plans and live changes.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{Computed: true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
			"name": schema.StringAttribute{Required: true},
			"kind": schema.StringAttribute{Required: true, Description: "required_tags, allowed_regions, " +
				"denied_instance_types, k8s_requests or max_monthly_increase."},
			"params_json": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("{}"),
				Description: `The kind's settings as JSON, e.g. jsonencode({ tags = ["team", "env"] }).`},
			"resource_types": schema.ListAttribute{ElementType: types.StringType, Optional: true, Computed: true,
				Default: listdefault.StaticValue(types.ListValueMust(types.StringType, []attr.Value{}))},
			"severity": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("warning"),
				Description: "info, warning or critical (critical fails Terraform plans in CI)."},
			"enabled": schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(true)},
		},
	}
}

func (r *policyResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = clientFrom(req.ProviderData, resp)
}

func (r *policyResource) body(ctx context.Context, m *policyModel) (map[string]any, error) {
	params, err := decodeJSON(m.ParamsJSON.ValueString(), map[string]any{})
	if err != nil {
		return nil, fmt.Errorf("params_json: %w", err)
	}
	var types_ []string
	if d := m.ResourceTypes.ElementsAs(ctx, &types_, false); d.HasError() {
		return nil, fmt.Errorf("resource_types: not a list of strings")
	}
	if types_ == nil {
		types_ = []string{}
	}
	return map[string]any{
		"name": m.Name.ValueString(), "kind": m.Kind.ValueString(), "params": params,
		"resource_types": types_, "severity": m.Severity.ValueString(), "enabled": m.Enabled.ValueBool(),
	}, nil
}

func (r *policyResource) apply(m *policyModel, got map[string]any) {
	m.ID = types.StringValue(fmt.Sprint(got["id"]))
	m.Name = types.StringValue(fmt.Sprint(got["name"]))
	m.Kind = types.StringValue(fmt.Sprint(got["kind"]))
	m.ParamsJSON = types.StringValue(keepOrReplace(m.ParamsJSON.ValueString(), got["params"]))
	var rt []attr.Value
	for _, t := range asList(got["resource_types"]) {
		rt = append(rt, types.StringValue(fmt.Sprint(t)))
	}
	m.ResourceTypes = types.ListValueMust(types.StringType, orEmpty(rt))
	m.Severity = types.StringValue(fmt.Sprint(got["severity"]))
	if b, ok := got["enabled"].(bool); ok {
		m.Enabled = types.BoolValue(b)
	}
}

func (r *policyResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var m policyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(ctx, &m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid policy", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPost, "/api/v1/governance/policies", body, &got); err != nil {
		resp.Diagnostics.AddError("Could not create the policy", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *policyResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var m policyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	got, err := r.client.findByID(ctx, "/api/v1/governance/policies", m.ID.ValueString())
	if errors.Is(err, ErrNotFound) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Could not read the policy", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *policyResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var m, state policyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(ctx, &m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid policy", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPut, "/api/v1/governance/policies/"+state.ID.ValueString(), body, &got); err != nil {
		resp.Diagnostics.AddError("Could not update the policy", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *policyResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var m policyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	err := r.client.Do(ctx, http.MethodDelete, "/api/v1/governance/policies/"+m.ID.ValueString(), nil, nil)
	if err != nil && !errors.Is(err, ErrNotFound) {
		resp.Diagnostics.AddError("Could not delete the policy", err.Error())
	}
}

func (r *policyResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}
