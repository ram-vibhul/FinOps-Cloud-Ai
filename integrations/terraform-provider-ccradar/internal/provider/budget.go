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

type budgetResource struct{ client *Client }

type budgetModel struct {
	ID              types.String  `tfsdk:"id"`
	Name            types.String  `tfsdk:"name"`
	Amount          types.Float64 `tfsdk:"amount"`
	Currency        types.String  `tfsdk:"currency"`
	Period          types.String  `tfsdk:"period"`
	Metric          types.String  `tfsdk:"metric"`
	ParentID        types.String  `tfsdk:"parent_id"`
	FiltersJSON     types.String  `tfsdk:"filters_json"`
	Thresholds      types.List    `tfsdk:"thresholds"`
	AlertOnForecast types.Bool    `tfsdk:"alert_on_forecast"`
	Recipients      types.List    `tfsdk:"recipients"`
}

// NewBudgetResource is the ccradar_budget resource.
func NewBudgetResource() resource.Resource { return &budgetResource{} }

func (r *budgetResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_budget"
}

func (r *budgetResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A budget, tracked with a forecast and alerting at its thresholds.",
		Attributes: map[string]schema.Attribute{
			"id": schema.StringAttribute{Computed: true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()}},
			"name":     schema.StringAttribute{Required: true},
			"amount":   schema.Float64Attribute{Required: true, Description: "The budget for one period."},
			"currency": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("USD")},
			"period": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("monthly"),
				Description: "monthly, quarterly or annual."},
			"metric": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("effective"),
				Description: "effective (after discounts) or billed."},
			"parent_id": schema.StringAttribute{Optional: true, Description: "The budget this one sits under."},
			"filters_json": schema.StringAttribute{Optional: true, Computed: true, Default: stringdefault.StaticString("{}"),
				Description: `Which costs count, as JSON: jsonencode({ service = ["Amazon Elastic Compute Cloud"] }).`},
			"thresholds": schema.ListAttribute{ElementType: types.Int64Type, Optional: true, Computed: true,
				Default: listdefault.StaticValue(types.ListValueMust(types.Int64Type,
					[]attr.Value{types.Int64Value(80), types.Int64Value(100)})),
				Description: "Alert at these percentages of the budget."},
			"alert_on_forecast": schema.BoolAttribute{Optional: true, Computed: true, Default: booldefault.StaticBool(true)},
			"recipients": schema.ListAttribute{ElementType: types.StringType, Optional: true, Computed: true,
				Default:     listdefault.StaticValue(types.ListValueMust(types.StringType, []attr.Value{})),
				Description: "Email addresses that get the alerts."},
		},
	}
}

func (r *budgetResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = clientFrom(req.ProviderData, resp)
}

func (r *budgetResource) body(ctx context.Context, m *budgetModel) (map[string]any, error) {
	filters, err := decodeJSON(m.FiltersJSON.ValueString(), map[string]any{})
	if err != nil {
		return nil, fmt.Errorf("filters_json: %w", err)
	}
	var thresholds []int64
	var recipients []string
	if d := m.Thresholds.ElementsAs(ctx, &thresholds, false); d.HasError() {
		return nil, fmt.Errorf("thresholds: not a list of numbers")
	}
	if d := m.Recipients.ElementsAs(ctx, &recipients, false); d.HasError() {
		return nil, fmt.Errorf("recipients: not a list of strings")
	}
	if recipients == nil {
		recipients = []string{}
	}
	b := map[string]any{
		"name": m.Name.ValueString(), "amount": m.Amount.ValueFloat64(),
		"currency": m.Currency.ValueString(), "period": m.Period.ValueString(),
		"metric": m.Metric.ValueString(), "filters": filters, "thresholds": thresholds,
		"alert_on_forecast": m.AlertOnForecast.ValueBool(), "recipients": recipients,
	}
	if !m.ParentID.IsNull() && m.ParentID.ValueString() != "" {
		b["parent_id"] = m.ParentID.ValueString()
	}
	return b, nil
}

func (r *budgetResource) apply(ctx context.Context, m *budgetModel, got map[string]any) {
	m.ID = types.StringValue(fmt.Sprint(got["id"]))
	m.Name = types.StringValue(fmt.Sprint(got["name"]))
	if v, ok := got["amount"].(float64); ok {
		m.Amount = types.Float64Value(v)
	}
	m.Currency = types.StringValue(fmt.Sprint(got["currency"]))
	m.Period = types.StringValue(fmt.Sprint(got["period"]))
	m.Metric = types.StringValue(fmt.Sprint(got["metric"]))
	if p, ok := got["parent_id"].(string); ok && p != "" {
		m.ParentID = types.StringValue(p)
	} else {
		m.ParentID = types.StringNull()
	}
	m.FiltersJSON = types.StringValue(keepOrReplace(m.FiltersJSON.ValueString(), got["filters"]))
	var th []attr.Value
	for _, t := range asList(got["thresholds"]) {
		if f, ok := t.(float64); ok {
			th = append(th, types.Int64Value(int64(f)))
		}
	}
	m.Thresholds = types.ListValueMust(types.Int64Type, orEmpty(th))
	if b, ok := got["alert_on_forecast"].(bool); ok {
		m.AlertOnForecast = types.BoolValue(b)
	}
	var rc []attr.Value
	for _, e := range asList(got["recipients"]) {
		rc = append(rc, types.StringValue(fmt.Sprint(e)))
	}
	m.Recipients = types.ListValueMust(types.StringType, orEmpty(rc))
}

func (r *budgetResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var m budgetModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(ctx, &m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid budget", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPost, "/api/v1/budgets", body, &got); err != nil {
		resp.Diagnostics.AddError("Could not create the budget", err.Error())
		return
	}
	r.apply(ctx, &m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *budgetResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var m budgetModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	got, err := r.client.findByID(ctx, "/api/v1/budgets", m.ID.ValueString())
	if errors.Is(err, ErrNotFound) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.AddError("Could not read the budget", err.Error())
		return
	}
	r.apply(ctx, &m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *budgetResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var m, state budgetModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &m)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	body, err := r.body(ctx, &m)
	if err != nil {
		resp.Diagnostics.AddError("Invalid budget", err.Error())
		return
	}
	var got map[string]any
	if err := r.client.Do(ctx, http.MethodPut, "/api/v1/budgets/"+state.ID.ValueString(), body, &got); err != nil {
		resp.Diagnostics.AddError("Could not update the budget", err.Error())
		return
	}
	r.apply(ctx, &m, got)
	resp.Diagnostics.Append(resp.State.Set(ctx, &m)...)
}

func (r *budgetResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var m budgetModel
	resp.Diagnostics.Append(req.State.Get(ctx, &m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	err := r.client.Do(ctx, http.MethodDelete, "/api/v1/budgets/"+m.ID.ValueString(), nil, nil)
	if err != nil && !errors.Is(err, ErrNotFound) {
		resp.Diagnostics.AddError("Could not delete the budget", err.Error())
	}
}

func (r *budgetResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("id"), req, resp)
}

func asList(v any) []any {
	l, _ := v.([]any)
	return l
}

func orEmpty(v []attr.Value) []attr.Value {
	if v == nil {
		return []attr.Value{}
	}
	return v
}
