package provider

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
)

// fakeAPI records requests and answers from a small in-memory store.
type fakeAPI struct {
	t        *testing.T
	requests []string
	bodies   []map[string]any
	answer   map[string]any
	list     []map[string]any
}

func (f *fakeAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer clk_test" {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"title":"Unauthorized","detail":"bad key"}`))
		return
	}
	f.requests = append(f.requests, r.Method+" "+r.URL.Path)
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	f.bodies = append(f.bodies, body)
	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.Method == http.MethodGet:
		_ = json.NewEncoder(w).Encode(f.list)
	case r.Method == http.MethodDelete:
		w.WriteHeader(http.StatusNoContent)
	default:
		_ = json.NewEncoder(w).Encode(f.answer)
	}
}

func setup(t *testing.T, f *fakeAPI) *Client {
	f.t = t
	srv := httptest.NewServer(f)
	t.Cleanup(srv.Close)
	c, err := NewClient(strings.Replace(srv.URL, "127.0.0.1", "localhost", 1), "clk_test", "test")
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func schemaOf(t *testing.T, r resource.Resource) tfsdk.State {
	var resp resource.SchemaResponse
	r.Schema(context.Background(), resource.SchemaRequest{}, &resp)
	if resp.Diagnostics.HasError() {
		t.Fatal(resp.Diagnostics)
	}
	s := resp.Schema
	return tfsdk.State{Schema: s, Raw: tftypes.NewValue(s.Type().TerraformType(context.Background()), nil)}
}

func TestProviderSchemaIsValid(t *testing.T) {
	server, err := providerserver.NewProtocol6WithError(New("test")())()
	if err != nil {
		t.Fatal(err)
	}
	resp, err := server.GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range resp.Diagnostics {
		t.Errorf("%s: %s", d.Summary, d.Detail)
	}
	for _, name := range []string{"ccradar_budget", "ccradar_policy", "ccradar_virtual_tag"} {
		if _, ok := resp.ResourceSchemas[name]; !ok {
			t.Errorf("missing resource %s", name)
		}
	}
}

func TestClientChecksEndpointAndKey(t *testing.T) {
	for _, bad := range [][2]string{
		{"http://ccradar.example.com", "clk_x"},
		{"not a url", "clk_x"},
		{"https://ccradar.example.com", "secret"},
	} {
		if _, err := NewClient(bad[0], bad[1], "t"); err == nil {
			t.Errorf("accepted %v", bad)
		}
	}
	if _, err := NewClient("https://ccradar.example.com/", "clk_x", "t"); err != nil {
		t.Error(err)
	}
}

func TestClientReportsTheProblem(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"title":"Bad Request","detail":"A budget cannot sit under itself."}`))
	}))
	defer srv.Close()
	c, _ := NewClient(strings.Replace(srv.URL, "127.0.0.1", "localhost", 1), "clk_test", "t")
	err := c.Do(context.Background(), http.MethodPost, "/api/v1/budgets", map[string]any{}, nil)
	if err == nil || !strings.Contains(err.Error(), "cannot sit under itself") {
		t.Fatalf("got %v", err)
	}
}

func TestBudgetCreateSendsTheBodyAndKeepsFilters(t *testing.T) {
	f := &fakeAPI{answer: map[string]any{
		"id": "b1", "name": "Web", "amount": 500.0, "currency": "USD", "period": "monthly",
		"metric": "effective", "parent_id": nil, "filters": map[string]any{"service": []any{"EC2"}},
		"thresholds": []any{50.0, 100.0}, "alert_on_forecast": true, "recipients": []any{"a@b.com"},
	}}
	r := &budgetResource{client: setup(t, f)}
	state := schemaOf(t, r)
	plan := tfsdk.Plan{Schema: state.Schema}
	ctx := context.Background()
	plan.Set(ctx, &budgetModel{
		ID: types.StringUnknown(), Name: types.StringValue("Web"), Amount: types.Float64Value(500),
		Currency: types.StringValue("USD"), Period: types.StringValue("monthly"),
		Metric: types.StringValue("effective"), ParentID: types.StringNull(),
		FiltersJSON:     types.StringValue(`{ "service": ["EC2"] }`),
		Thresholds:      types.ListValueMust(types.Int64Type, []attr.Value{types.Int64Value(50), types.Int64Value(100)}),
		AlertOnForecast: types.BoolValue(true),
		Recipients:      types.ListValueMust(types.StringType, []attr.Value{types.StringValue("a@b.com")}),
	})
	resp := &resource.CreateResponse{State: state}
	r.Create(ctx, resource.CreateRequest{Plan: plan}, resp)
	if resp.Diagnostics.HasError() {
		t.Fatal(resp.Diagnostics)
	}
	var got budgetModel
	resp.State.Get(ctx, &got)
	if got.ID.ValueString() != "b1" || got.FiltersJSON.ValueString() != `{ "service": ["EC2"] }` {
		t.Errorf("state: %+v", got)
	}
	if f.requests[0] != "POST /api/v1/budgets" || f.bodies[0]["amount"] != 500.0 {
		t.Errorf("request: %v %v", f.requests, f.bodies)
	}
	if _, ok := f.bodies[0]["parent_id"]; ok {
		t.Error("parent_id sent when not set")
	}
}

func TestReadRemovesWhatWasDeletedOutsideTerraform(t *testing.T) {
	f := &fakeAPI{list: []map[string]any{{"id": "other"}}}
	r := &policyResource{client: setup(t, f)}
	state := schemaOf(t, r)
	ctx := context.Background()
	state.Set(ctx, &policyModel{
		ID: types.StringValue("p1"), Name: types.StringValue("Tags"), Kind: types.StringValue("required_tags"),
		ParamsJSON:    types.StringValue(`{"tags":["team"]}`),
		ResourceTypes: types.ListValueMust(types.StringType, []attr.Value{}),
		Severity:      types.StringValue("warning"), Enabled: types.BoolValue(true),
	})
	resp := &resource.ReadResponse{State: state}
	r.Read(ctx, resource.ReadRequest{State: state}, resp)
	if resp.Diagnostics.HasError() || !resp.State.Raw.IsNull() {
		t.Fatalf("expected removal: %v %v", resp.Diagnostics, resp.State.Raw)
	}
	if f.requests[0] != "GET /api/v1/governance/policies" {
		t.Errorf("request: %v", f.requests)
	}
}

func TestVirtualTagReadShowsOutsideChanges(t *testing.T) {
	rules := []any{map[string]any{"value": "web", "match": map[string]any{"service": []any{"EC2"}, "region": []any{}}}}
	f := &fakeAPI{list: []map[string]any{{"id": "v1", "key": "team", "description": "", "default_value": nil,
		"override_existing": false, "rules": rules}}}
	r := &virtualTagResource{client: setup(t, f)}
	state := schemaOf(t, r)
	ctx := context.Background()
	m := virtualTagModel{ID: types.StringValue("v1"), Key: types.StringValue("team"),
		Description: types.StringValue(""), DefaultValue: types.StringNull(),
		OverrideExisting: types.BoolValue(false),
		RulesJSON:        types.StringValue(`[{"value":"web","match":{"service":["EC2"]}}]`)}
	state.Set(ctx, &m)
	resp := &resource.ReadResponse{State: state}
	r.Read(ctx, resource.ReadRequest{State: state}, resp)
	var got virtualTagModel
	resp.State.Get(ctx, &got)
	if got.RulesJSON.ValueString() != m.RulesJSON.ValueString() {
		t.Errorf("same meaning should keep the text, got %s", got.RulesJSON.ValueString())
	}
	rules[0].(map[string]any)["value"] = "shop" // changed in the app
	resp = &resource.ReadResponse{State: state}
	r.Read(ctx, resource.ReadRequest{State: state}, resp)
	resp.State.Get(ctx, &got)
	if !strings.Contains(got.RulesJSON.ValueString(), `"shop"`) {
		t.Errorf("outside change not shown: %s", got.RulesJSON.ValueString())
	}
}
