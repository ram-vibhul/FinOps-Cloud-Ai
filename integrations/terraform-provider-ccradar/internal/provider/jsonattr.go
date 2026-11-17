package provider

import (
	"encoding/json"
	"fmt"
	"reflect"
)

// canon drops nulls and empty lists, maps and strings, so that what the user wrote
// and what the API answers compare equal when they mean the same.
func canon(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := map[string]any{}
		for k, x := range t {
			if c := canon(x); c != nil {
				out[k] = c
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case []any:
		out := make([]any, 0, len(t))
		for _, x := range t {
			out = append(out, canon(x))
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case string:
		if t == "" {
			return nil
		}
		return t
	default:
		return v
	}
}

// decodeJSON parses a JSON attribute; empty means the given fallback.
func decodeJSON(text string, fallback any) (any, error) {
	if text == "" {
		return fallback, nil
	}
	var v any
	if err := json.Unmarshal([]byte(text), &v); err != nil {
		return nil, fmt.Errorf("not valid JSON: %w", err)
	}
	return v, nil
}

// keepOrReplace returns the text in state when the API value means the same, else the
// API value as compact JSON (so a change made outside Terraform shows as a diff).
func keepOrReplace(current string, fromAPI any) string {
	if v, err := decodeJSON(current, nil); current != "" && err == nil && reflect.DeepEqual(canon(v), canon(fromAPI)) {
		return current
	}
	b, _ := json.Marshal(fromAPI)
	return string(b)
}
