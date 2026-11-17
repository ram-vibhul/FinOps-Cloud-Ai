package provider

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ErrNotFound is returned for a 404 answer.
var ErrNotFound = errors.New("not found")

// Client calls the CloudCostRadar REST API with an organisation API key.
type Client struct {
	base  *url.URL
	key   string
	agent string
	http  *http.Client
}

// NewClient checks the endpoint (https, or http on localhost for testing).
func NewClient(endpoint, key, version string) (*Client, error) {
	u, err := url.Parse(strings.TrimRight(endpoint, "/"))
	if err != nil || u.Host == "" {
		return nil, fmt.Errorf("endpoint must be a URL such as https://ccradar.example.com")
	}
	local := u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1"
	if u.Scheme != "https" && !(u.Scheme == "http" && local) {
		return nil, fmt.Errorf("endpoint must use https")
	}
	if !strings.HasPrefix(key, "clk_") {
		return nil, fmt.Errorf("api_key must be an organisation API key (it starts with clk_)")
	}
	return &Client{
		base:  u,
		key:   key,
		agent: "terraform-provider-ccradar/" + version,
		http:  &http.Client{Timeout: 60 * time.Second},
	}, nil
}

// Do sends body as JSON (when not nil) and decodes the answer into out (when not nil).
func (c *Client) Do(ctx context.Context, method, path string, body, out any) error {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base.String()+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.key)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.agent)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("could not reach CloudCostRadar: %w", err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 10<<20))
	if resp.StatusCode == http.StatusNotFound {
		return ErrNotFound
	}
	if resp.StatusCode >= 400 {
		var problem struct {
			Title  string `json:"title"`
			Detail any    `json:"detail"`
		}
		_ = json.Unmarshal(raw, &problem)
		msg := problem.Title
		if s, ok := problem.Detail.(string); ok && s != "" {
			msg = s
		} else if problem.Detail != nil {
			b, _ := json.Marshal(problem.Detail)
			msg = strings.TrimSpace(msg + " " + string(b))
		}
		if msg == "" {
			msg = strings.TrimSpace(string(raw))
		}
		return fmt.Errorf("CloudCostRadar answered %d: %s", resp.StatusCode, msg)
	}
	if out != nil && len(raw) > 0 {
		return json.Unmarshal(raw, out)
	}
	return nil
}

// findByID lists a collection and returns the item with this id, or ErrNotFound.
func (c *Client) findByID(ctx context.Context, path, id string) (map[string]any, error) {
	var items []map[string]any
	if err := c.Do(ctx, http.MethodGet, path, nil, &items); err != nil {
		return nil, err
	}
	for _, it := range items {
		if fmt.Sprint(it["id"]) == id {
			return it, nil
		}
	}
	return nil, ErrNotFound
}
