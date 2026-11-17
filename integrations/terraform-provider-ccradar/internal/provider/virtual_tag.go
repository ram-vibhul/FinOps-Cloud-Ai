package provider

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/types"
)

type virtualTagResource struct{ client *Client }

type virtualTagModel struct {
	ID               types.String `tfsdk:"id"`
	Key              types.String `tfsdk:"key"`
	Description      types.String `tfsdk:"description"`
	DefaultValue     types.String `tfsdk:"default_value"`
	OverrideExisting types.Bool   `tfsdk:"override_existing"`
	RulesJSON        types.String `tfsdk:"rules_json"`
}

// NewVirtualTagResource is the ccradar_virtual_tag resource.
func NewVirtualTagResource() resource.Resource { return &virtualTagResource{} }

func (r *virtualTagResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_virtual_tag"
}

func (r *virtualTagResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A virtual tag: a tag worked out from rules over the bill, without touching the cloud.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{Computed: true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
			"key":           schema.StringAttribute{Required: true},
			"description":   schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("")},
			"default_value": schema.StringAttribute{Optional: true},
			"override_existing": schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(false),
				Description: "Replace a real tag of the same key when a rule matches."},
			"rules_json": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("[]"),
				Description: `Rules in order, as JSON: jsonencode([{ value = "web", match = { service = ["Amazon Elastic Compute Cloud"] } }]).`},
		},
	}
}

func (r *virtualTagResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = clientFrom(req.ProviderData, resp)
}

func (r *virtualTagResource) body(m *virtualTagModel) (map[string]any, error) {
	rules, err := decodeJSON(m.RulesJSON.ValueString(), []any{})
	if err != nil {
		return nil, fmt.Errorf("rules_json: %w", err)
	}
	b := map[string]any{
		"key": m.Key.ValueString(), "description": m.Description.ValueString(),
		"override_existing": m.OverrideExisting.ValueBool(), "rules": rules,
	}
	if !m.DefaultValue.IsNull() {
		b["default_value"] = m.DefaultValue.ValueString()
	}
	return b, nil
}

func (r *virtualTagResource) apply(m *virtualTagModel, got map[string]any) {
	m.ID = types.StringValue(fmt.Sprint(got["id"]))
	m.Key = types.StringValue(fmt.Sprint(got["key"]))
	m.Description = types.StringValue(fmt.Sprint(got["description"]))
	if d, ok := got["default_value"].(string); ok {
		m.DefaultValue = types.StringValue(d)
	} else {
		m.DefaultValue = types.StringNull()
	}
	if b, ok := got["override_existing"].(bool); ok {
		m.OverrideExisting = types.BoolValue(b)
	}
	m.RulesJSON = types.StringValue(keepOrReplace(m.RulesJSON.ValueString(), got["rules"]))
}

func (r *virtualTagResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var m virtualTagModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(&m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid virtual tag", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPost, "/api/v1/virtual-tags", body, &got); err != nil {
		resp.Diagnostics.AddError("Could not create the virtual tag", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *virtualTagResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var m virtualTagModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	got, err := r.client.findByID(ctx, "/api/v1/virtual-tags", m.ID.ValueString())
	if errors.Is(err, ErrNotFound) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Could not read the virtual tag", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *virtualTagResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var m, state virtualTagModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(&m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid virtual tag", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPut, "/api/v1/virtual-tags/"+state.ID.ValueString(), body, &got); err != nil {
		resp.Diagnostics.AddError("Could not update the virtual tag", err.Error())
		return
	}
	r.apply(&m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *virtualTagResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var m virtualTagModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	err := r.client.Do(ctx, http.MethodDelete, "/api/v1/virtual-tags/"+m.ID.ValueString(), nil, nil)
	if err != nil && !errors.Is(err, ErrNotFound) {
		resp.Diagnostics.AddError("Could not delete the virtual tag", err.Error())
	}
}

func (r *virtualTagResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}
