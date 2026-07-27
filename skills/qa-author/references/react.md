# React Testing Reference

Stack-specific patterns for adversarial testing of React applications.

## Philosophy

Test behavior, not implementation. Users don't know about state variables,
effect hooks, or component hierarchies — they know about clicking buttons and
seeing results.

## Rendering & Queries

### Prefer accessible queries

```typescript
// Good — queries the way a screen reader does
screen.getByRole('button', { name: /submit/i });
screen.getByLabelText(/email address/i);
screen.getByText(/no results found/i);

// Bad — brittle, tests implementation
container.querySelector('.submit-btn');
screen.getByTestId('submit-button'); // last resort only
```

### Query priority (Testing Library)

1. `getByRole` — accessible name/role (best)
2. `getByLabelText` — form fields
3. `getByPlaceholderText` — when no label exists
4. `getByText` — non-interactive content
5. `getByDisplayValue` — filled-in form fields
6. `getByAltText` — images
7. `getByTitle` — title attribute
8. `getByTestId` — last resort (no semantic meaning)

## User Interaction

### Always use `userEvent` over `fireEvent`

```typescript
import userEvent from '@testing-library/user-event';

const user = userEvent.setup();

// Good — simulates real user behavior (focus, keydown, keyup, input, change)
await user.click(screen.getByRole('button', { name: /save/i }));
await user.type(screen.getByLabelText(/email/i), 'test@example.com');
await user.selectOptions(screen.getByRole('combobox'), 'option-value');

// Bad — only fires a single synthetic event
fireEvent.click(button);
fireEvent.change(input, { target: { value: 'text' } });
```

### Keyboard navigation

```typescript
// Tab order matters for accessibility
await user.tab();
expect(screen.getByLabelText(/first name/i)).toHaveFocus();
await user.tab();
expect(screen.getByLabelText(/last name/i)).toHaveFocus();

// Escape to close
await user.keyboard('{Escape}');
expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
```

## Async Patterns

### Waiting for state updates

```typescript
// Good — waits for the element to appear
await waitFor(() => {
  expect(screen.getByText(/success/i)).toBeInTheDocument();
});

// Good — for elements that appear after async operations
const successMessage = await screen.findByText(/saved successfully/i);
expect(successMessage).toBeInTheDocument();

// Bad — arbitrary timeouts
await new Promise(resolve => setTimeout(resolve, 1000));
```

### Act warnings

If you see "act() warnings," the component is updating state outside of the
test's awareness. Fix by:
1. Using `waitFor` / `findBy` for async updates
2. Ensuring all promises resolve before assertions

```typescript
// If a component fetches on mount:
render(<UserProfile userId="123" />);
// Wait for loading to finish
await waitFor(() => {
  expect(screen.queryByText(/loading/i)).not.toBeInTheDocument();
});
// Now assert on the loaded state
expect(screen.getByText(/John Doe/i)).toBeInTheDocument();
```

## Adversarial Patterns for React

### Error boundary testing

```typescript
// Verify error boundaries catch and display fallback UI
const ThrowingChild = () => { throw new Error('render failure'); };

render(
  <ErrorBoundary fallback={<div>Something went wrong</div>}>
    <ThrowingChild />
  </ErrorBoundary>
);

expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
// Verify the error didn't propagate to the whole page
expect(screen.queryByText(/unhandled/i)).not.toBeInTheDocument();
```

### Loading / error / empty states

```typescript
// Every data-fetching component has three states — test all three
// 1. Loading
render(<UserList />);
expect(screen.getByText(/loading/i)).toBeInTheDocument();

// 2. Error
server.use(rest.get('/api/users', (req, res, ctx) => res(ctx.status(500))));
render(<UserList />);
await waitFor(() => {
  expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
});

// 3. Empty
server.use(rest.get('/api/users', (req, res, ctx) => res(ctx.json([]))));
render(<UserList />);
await waitFor(() => {
  expect(screen.getByText(/no users found/i)).toBeInTheDocument();
});
```

### Form validation edge cases

```typescript
// Rapid submission (double-click prevention)
const submitButton = screen.getByRole('button', { name: /submit/i });
await user.click(submitButton);
await user.click(submitButton); // immediate second click
// Should only submit once
expect(mockSubmit).toHaveBeenCalledTimes(1);

// Paste into validated field
await user.click(screen.getByLabelText(/phone/i));
await user.paste('not-a-phone-number');
await user.click(submitButton);
expect(screen.getByText(/invalid phone/i)).toBeInTheDocument();

// Unicode in text inputs
await user.type(screen.getByLabelText(/name/i), '');
// Verify it renders correctly, doesn't crash, doesn't truncate
expect(screen.getByLabelText(/name/i)).toHaveValue('');
```

### Accessibility assertions

```typescript
import { axe, toHaveNoViolations } from 'jest-axe';
expect.extend(toHaveNoViolations);

const { container } = render(<MyComponent />);
const results = await axe(container);
expect(results).toHaveNoViolations();

// Also verify ARIA states update correctly
const expandButton = screen.getByRole('button', { name: /show details/i });
expect(expandButton).toHaveAttribute('aria-expanded', 'false');
await user.click(expandButton);
expect(expandButton).toHaveAttribute('aria-expanded', 'true');
```

### Responsive / conditional rendering

```typescript
// Test that mobile-only elements aren't rendered on desktop viewport
// (if using media queries that affect DOM structure)
Object.defineProperty(window, 'innerWidth', { value: 1024 });
window.dispatchEvent(new Event('resize'));
render(<Navigation />);
expect(screen.queryByRole('button', { name: /menu/i })).not.toBeInTheDocument();
```

### Unmount / cleanup

```typescript
// Verify no memory leaks: subscriptions, timers, listeners cleaned up
const { unmount } = render(<RealTimeComponent />);
unmount();
// Advance timers — should not throw "setState on unmounted component"
jest.advanceTimersByTime(5000);
// No console.error about unmounted state updates
expect(consoleSpy).not.toHaveBeenCalled();
```

## Anti-Patterns to Catch

- Snapshot tests as the sole assertion (they prove nothing broke, not that
  anything works)
- Testing state directly via component internals (e.g., accessing `.state`)
- Querying by class name or CSS selector
- `fireEvent` when `userEvent` is available
- Missing `await` on async interactions (tests pass but are non-deterministic)
- Testing that a mock was called instead of testing the visible effect
- Not testing disabled/loading states of buttons during async operations
