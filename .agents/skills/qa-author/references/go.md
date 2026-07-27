# Go Testing Reference

Stack-specific patterns for adversarial testing of Go applications.

## Table-Driven Tests

The standard Go pattern. Every test function should start here.

```go
func TestParseUserID(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    int64
        wantErr bool
    }{
        {"valid", "123", 123, false},
        {"zero", "0", 0, false},
        {"negative", "-1", 0, true},
        {"empty string", "", 0, true},
        {"whitespace", "  42  ", 0, true},    // or 42 if trimming is expected
        {"overflow", "9223372036854775808", 0, true},
        {"non-numeric", "abc", 0, true},
        {"float", "3.14", 0, true},
        {"leading zero", "007", 7, false},    // verify octal isn't assumed
        {"max int64", "9223372036854775807", 9223372036854775807, false},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := ParseUserID(tt.input)
            if tt.wantErr {
                if err == nil {
                    t.Errorf("ParseUserID(%q) = %d, want error", tt.input, got)
                }
                return
            }
            if err != nil {
                t.Fatalf("ParseUserID(%q) unexpected error: %v", tt.input, err)
            }
            if got != tt.want {
                t.Errorf("ParseUserID(%q) = %d, want %d", tt.input, got, tt.want)
            }
        })
    }
}
```

## Parallel Tests

```go
func TestConcurrentAccess(t *testing.T) {
    t.Parallel() // marks this test as safe to run in parallel

    tests := []struct{ name, input string }{...}
    for _, tt := range tests {
        tt := tt // capture range variable (required pre-Go 1.22)
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel() // subtests run in parallel with each other
            // ...
        })
    }
}
```

Use `t.Parallel()` aggressively to surface race conditions. Run with
`-race` flag: `go test -race ./...`

## Interface Mocks

Prefer hand-written mocks over framework magic. A mock is just a struct that
implements the interface.

```go
type mockStore struct {
    getUserFunc func(ctx context.Context, id string) (*User, error)
    calls       []string // track what was called
}

func (m *mockStore) GetUser(ctx context.Context, id string) (*User, error) {
    m.calls = append(m.calls, "GetUser:"+id)
    if m.getUserFunc != nil {
        return m.getUserFunc(ctx, id)
    }
    return nil, errors.New("not configured")
}
```

This makes test behavior explicit and avoids mock framework DSL overhead.

## HTTP Handler Testing

```go
func TestGetUserHandler(t *testing.T) {
    // Setup
    store := &mockStore{
        getUserFunc: func(ctx context.Context, id string) (*User, error) {
            if id == "existing" {
                return &User{ID: "existing", Name: "Alice"}, nil
            }
            return nil, ErrNotFound
        },
    }
    handler := NewUserHandler(store)

    tests := []struct {
        name       string
        method     string
        path       string
        wantStatus int
        wantBody   string
    }{
        {"happy path", "GET", "/users/existing", 200, `"name":"Alice"`},
        {"not found", "GET", "/users/missing", 404, `"error"`},
        {"wrong method", "POST", "/users/existing", 405, ""},
        {"empty id", "GET", "/users/", 400, ""},
        {"path traversal", "GET", "/users/../admin", 400, ""},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            req := httptest.NewRequest(tt.method, tt.path, nil)
            rec := httptest.NewRecorder()

            handler.ServeHTTP(rec, req)

            if rec.Code != tt.wantStatus {
                t.Errorf("status = %d, want %d", rec.Code, tt.wantStatus)
            }
            if tt.wantBody != "" && !strings.Contains(rec.Body.String(), tt.wantBody) {
                t.Errorf("body = %q, want substring %q", rec.Body.String(), tt.wantBody)
            }
        })
    }
}
```

### Test server for integration tests

```go
func TestClientIntegration(t *testing.T) {
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        switch {
        case r.URL.Path == "/api/users" && r.Method == "GET":
            w.Header().Set("Content-Type", "application/json")
            json.NewEncoder(w).Encode([]User{{ID: "1", Name: "Alice"}})
        default:
            w.WriteHeader(404)
        }
    }))
    defer srv.Close()

    client := NewAPIClient(srv.URL)
    users, err := client.ListUsers(context.Background())
    // ...
}
```

## Context & Cancellation

```go
func TestOperationRespectsContext(t *testing.T) {
    ctx, cancel := context.WithCancel(context.Background())
    cancel() // already cancelled

    _, err := service.DoExpensiveThing(ctx)
    if !errors.Is(err, context.Canceled) {
        t.Errorf("expected context.Canceled, got %v", err)
    }
}

func TestOperationTimeout(t *testing.T) {
    ctx, cancel := context.WithTimeout(context.Background(), 1*time.Millisecond)
    defer cancel()

    time.Sleep(5 * time.Millisecond) // ensure timeout fires
    _, err := service.DoThing(ctx)
    if !errors.Is(err, context.DeadlineExceeded) {
        t.Errorf("expected DeadlineExceeded, got %v", err)
    }
}
```

## Error Wrapping & Sentinel Checks

```go
// Verify errors are properly wrapped for caller inspection
func TestErrorWrapping(t *testing.T) {
    _, err := repo.GetUser(ctx, "nonexistent")

    // Caller should be able to check the sentinel
    if !errors.Is(err, ErrNotFound) {
        t.Errorf("expected ErrNotFound, got %v", err)
    }

    // Verify context is preserved in the chain
    var notFoundErr *NotFoundError
    if !errors.As(err, &notFoundErr) {
        t.Fatalf("expected *NotFoundError in chain, got %T", err)
    }
    if notFoundErr.Resource != "user" {
        t.Errorf("resource = %q, want %q", notFoundErr.Resource, "user")
    }
}
```

## Adversarial Patterns for Go

### Race condition detection

```go
// Run with: go test -race -count=100 ./...
func TestConcurrentMapAccess(t *testing.T) {
    cache := NewCache()
    var wg sync.WaitGroup
    for i := 0; i < 100; i++ {
        wg.Add(2)
        go func(n int) {
            defer wg.Done()
            cache.Set(fmt.Sprintf("key-%d", n), n)
        }(i)
        go func(n int) {
            defer wg.Done()
            cache.Get(fmt.Sprintf("key-%d", n))
        }(i)
    }
    wg.Wait()
}
```

### Goroutine leak detection

```go
import "go.uber.org/goleak"

func TestMain(m *testing.M) {
    goleak.VerifyTestMain(m)
}
```

### Nil receiver

```go
func TestNilReceiver(t *testing.T) {
    var s *Service // nil
    // Should panic or return error, not silently misbehave
    defer func() {
        if r := recover(); r == nil {
            t.Error("expected panic on nil receiver, got none")
        }
    }()
    s.DoThing()
}
```

### JSON marshaling edge cases

```go
func TestJSONRoundTrip(t *testing.T) {
    cases := []struct {
        name  string
        input User
    }{
        {"zero value", User{}},
        {"unicode name", User{Name: ""}},
        {"null-byte in string", User{Name: "foo\x00bar"}},
        {"max-length name", User{Name: strings.Repeat("a", 10000)}},
    }
    for _, tt := range cases {
        t.Run(tt.name, func(t *testing.T) {
            data, err := json.Marshal(tt.input)
            if err != nil {
                t.Fatalf("marshal: %v", err)
            }
            var got User
            if err := json.Unmarshal(data, &got); err != nil {
                t.Fatalf("unmarshal: %v", err)
            }
            if diff := cmp.Diff(tt.input, got); diff != "" {
                t.Errorf("roundtrip mismatch (-want +got):\n%s", diff)
            }
        })
    }
}
```

## Anti-Patterns to Catch

- Tests without `t.Run` subtests (can't identify which case failed)
- Missing `t.Parallel()` on independent tests (hides race conditions)
- Using `t.Fatal` in goroutines (panics, doesn't fail the test properly)
- Asserting on string representations of errors instead of `errors.Is`/`errors.As`
- Tests that depend on file system state without `t.TempDir()`
- Using `time.Sleep` instead of channels/contexts for synchronization
- Not running with `-race` flag in CI
