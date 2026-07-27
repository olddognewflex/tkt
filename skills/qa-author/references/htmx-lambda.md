# HTMX + Lambda Testing Reference

Stack-specific patterns for adversarial testing of HTMX applications served by
AWS Lambda handlers. This pattern is uncommon — most internet testing guidance
doesn't cover it. Reference the actual request/response cycle.

## Architecture

```
Browser → API Gateway → Lambda Handler → HTML Fragment Response
         (hx-get/post)                    (not JSON, not full page)
```

Key differences from typical API testing:
- Responses are **HTML fragments**, not JSON
- `hx-*` attributes drive client behavior — they ARE the API contract
- `HX-Trigger` response headers drive event-driven UI updates
- No SPA router — server decides what HTML to swap

## HTML Fragment Assertions

Use cheerio (Node.js) or jsdom to parse and assert on HTML responses.

```typescript
import * as cheerio from 'cheerio';

test('GET /users returns user list fragment', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users',
    headers: { 'HX-Request': 'true' },
  }));

  expect(response.statusCode).toBe(200);
  expect(response.headers['Content-Type']).toMatch(/text\/html/);

  const $ = cheerio.load(response.body);

  // Assert structure, not exact HTML string
  expect($('ul#user-list li')).toHaveLength(3);
  expect($('li').first().text()).toContain('Alice');

  // Verify hx-* attributes — these are the API contract
  expect($('li').first().attr('hx-get')).toBe('/users/1');
  expect($('li').first().attr('hx-target')).toBe('#user-detail');
  expect($('li').first().attr('hx-swap')).toBe('innerHTML');
});
```

## HX-Trigger Header Assertions

`HX-Trigger` response headers tell the client to fire events. These are part of
the contract — test them explicitly.

```typescript
test('POST /users triggers userAdded event', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'POST',
    path: '/users',
    body: JSON.stringify({ name: 'Bob', email: 'bob@example.com' }),
    headers: {
      'HX-Request': 'true',
      'Content-Type': 'application/json',
    },
  }));

  expect(response.statusCode).toBe(201);

  // HX-Trigger can be a string (event name) or JSON (event + data)
  const trigger = JSON.parse(response.headers['HX-Trigger']);
  expect(trigger).toHaveProperty('userAdded');
  expect(trigger.userAdded).toMatchObject({ id: expect.any(String) });
});

test('DELETE /users/:id triggers userList refresh', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'DELETE',
    path: '/users/123',
    headers: { 'HX-Request': 'true' },
  }));

  expect(response.statusCode).toBe(200);
  // After delete, the response should trigger a list refresh
  expect(response.headers['HX-Trigger']).toContain('refreshUserList');
});
```

## hx-* Attribute Testing

The `hx-*` attributes in rendered HTML ARE the client-side routing. Test them
like you'd test API routes.

```typescript
test('edit form has correct hx-put target', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users/123/edit',
    headers: { 'HX-Request': 'true' },
  }));

  const $ = cheerio.load(response.body);
  const form = $('form');

  // These attributes define the mutation contract
  expect(form.attr('hx-put')).toBe('/users/123');
  expect(form.attr('hx-target')).toBe('#user-detail');
  expect(form.attr('hx-swap')).toBe('outerHTML');

  // Verify form inputs exist for all required fields
  expect($('input[name="name"]')).toHaveLength(1);
  expect($('input[name="email"]')).toHaveLength(1);

  // Verify CSRF token is present if applicable
  expect($('input[name="_csrf"]').val()).toBeTruthy();
});
```

## Template Rendering Assertions

Verify that data is actually bound into templates correctly.

```typescript
test('user detail renders all fields', async () => {
  // Setup: user exists in DB
  await createUser({ id: '123', name: 'Alice', email: 'alice@example.com', role: 'admin' });

  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users/123',
    headers: { 'HX-Request': 'true' },
  }));

  const $ = cheerio.load(response.body);

  // Verify data binding — not just that it rendered, but the RIGHT data
  expect($('[data-field="name"]').text()).toBe('Alice');
  expect($('[data-field="email"]').text()).toBe('alice@example.com');
  expect($('[data-field="role"]').text()).toBe('admin');

  // XSS: verify HTML entities are escaped in user content
  await updateUser('123', { name: '<script>alert("xss")</script>' });
  const xssResponse = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users/123',
    headers: { 'HX-Request': 'true' },
  }));
  expect(xssResponse.body).not.toContain('<script>');
  expect(xssResponse.body).toContain('&lt;script&gt;');
});
```

## Lambda Handler Integration Tests

Use the API Gateway event shape directly — no HTTP framework abstractions.

```typescript
// Helper to construct API Gateway proxy events
function apiGatewayEvent(opts: {
  method: string;
  path: string;
  body?: string;
  headers?: Record<string, string>;
  pathParameters?: Record<string, string>;
  queryStringParameters?: Record<string, string>;
}): APIGatewayProxyEvent {
  return {
    httpMethod: opts.method,
    path: opts.path,
    body: opts.body ?? null,
    headers: { 'HX-Request': 'true', ...opts.headers },
    pathParameters: opts.pathParameters ?? null,
    queryStringParameters: opts.queryStringParameters ?? null,
    // ... other required fields with defaults
  } as APIGatewayProxyEvent;
}
```

### Non-HTMX requests (progressive enhancement)

```typescript
test('non-HTMX request returns full page', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users',
    headers: {}, // No HX-Request header
  }));

  const $ = cheerio.load(response.body);
  // Full page should have html/head/body
  expect($('html')).toHaveLength(1);
  expect($('head title').text()).toBeTruthy();
  expect($('body #user-list')).toHaveLength(1);
});

test('HTMX request returns fragment only', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users',
    headers: { 'HX-Request': 'true' },
  }));

  const $ = cheerio.load(response.body);
  // Fragment should NOT have html/head/body wrappers
  expect($('html')).toHaveLength(0);
  expect($('head')).toHaveLength(0);
});
```

## Adversarial Patterns for HTMX + Lambda

### Swap target integrity

```typescript
// If hx-target references an element ID, that element must exist in the
// page context. A missing target = silent failure (no visible error).
test('all hx-target references resolve', async () => {
  // Get the full page first
  const pageResponse = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/dashboard',
    headers: {},
  }));
  const $page = cheerio.load(pageResponse.body);

  // Find all hx-target attributes in fragment responses
  const fragmentResponse = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users',
    headers: { 'HX-Request': 'true' },
  }));
  const $fragment = cheerio.load(fragmentResponse.body);

  $fragment('[hx-target]').each((_, el) => {
    const target = $fragment(el).attr('hx-target');
    if (target && target.startsWith('#')) {
      expect($page(target).length).toBeGreaterThan(0);
    }
  });
});
```

### Error responses as HTML

```typescript
// Errors must return HTML fragments (not JSON) for HTMX to display
test('validation error returns HTML error fragment', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'POST',
    path: '/users',
    body: JSON.stringify({ name: '', email: 'not-an-email' }),
    headers: { 'HX-Request': 'true', 'Content-Type': 'application/json' },
  }));

  // Should be 422, not 400 (HTMX doesn't swap on non-2xx by default unless
  // hx-target-error is set)
  expect(response.statusCode).toBe(422);
  expect(response.headers['Content-Type']).toMatch(/text\/html/);

  const $ = cheerio.load(response.body);
  expect($('.error-message')).toHaveLength(2); // name + email
  expect($('.error-message').first().text()).toContain('required');
});
```

### Out-of-band swaps (hx-swap-oob)

```typescript
test('response includes OOB update for notification area', async () => {
  const response = await handler(apiGatewayEvent({
    method: 'POST',
    path: '/users',
    body: JSON.stringify({ name: 'Alice', email: 'alice@example.com' }),
    headers: { 'HX-Request': 'true', 'Content-Type': 'application/json' },
  }));

  const $ = cheerio.load(response.body);
  // OOB element should target the toast area
  const oob = $('[hx-swap-oob="true"]');
  expect(oob).toHaveLength(1);
  expect(oob.attr('id')).toBe('notifications');
  expect(oob.text()).toContain('User created');
});
```

### Cold start / timeout behavior

```typescript
test('handler responds within API Gateway timeout', async () => {
  const start = Date.now();
  const response = await handler(apiGatewayEvent({
    method: 'GET',
    path: '/users',
    headers: { 'HX-Request': 'true' },
  }));
  const elapsed = Date.now() - start;

  // API Gateway default timeout is 29s; Lambda should be well under
  expect(elapsed).toBeLessThan(5000);
  expect(response.statusCode).toBe(200);
});
```

## Anti-Patterns to Catch

- Asserting on HTML string equality (brittle — whitespace, attribute order)
- Not testing the `HX-Request` header presence/absence fork
- Returning JSON error responses to HTMX requests (won't render)
- Missing `HX-Trigger` headers after mutations (client doesn't know to refresh)
- `hx-target` pointing to IDs that don't exist in the page context
- Not escaping user content in templates (XSS via HTML injection)
- Testing with framework abstractions instead of raw API Gateway events
- Ignoring `hx-swap` strategy in assertions (innerHTML vs outerHTML matters)
