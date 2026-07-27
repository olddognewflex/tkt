# NestJS Testing Reference

Stack-specific patterns for adversarial testing of NestJS applications.

## Unit Tests

### Module setup

```typescript
const module = await Test.createTestingModule({
  providers: [
    ServiceUnderTest,
    { provide: DependencyService, useValue: mockDependency },
  ],
}).compile();

const service = module.get<ServiceUnderTest>(ServiceUnderTest);
```

### Provider override patterns

```typescript
// Override a single provider for isolation
const module = await Test.createTestingModule({
  imports: [AppModule],
})
  .overrideProvider(DatabaseService)
  .useValue(mockDb)
  .compile();
```

### Guard testing in isolation

```typescript
// Test guards independently from controllers
const guard = new AuthGuard(mockReflector, mockAuthService);
const context = createMockExecutionContext({ user: null });
await expect(guard.canActivate(context)).rejects.toThrow(UnauthorizedException);
```

### Interceptor testing

```typescript
const interceptor = new TransformInterceptor();
const context = createMockExecutionContext();
const next = { handle: () => of(rawData) };
const result = await lastValueFrom(interceptor.intercept(context, next));
expect(result).toEqual(expectedTransformedShape);
```

## Integration / E2E Tests

### Supertest patterns

```typescript
const app = moduleFixture.createNestApplication();
// Apply the same pipes/guards as production
app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
await app.init();

const response = await request(app.getHttpServer())
  .post('/users')
  .send(payload)
  .expect(201);

// Assert response SHAPE, not just status
expect(response.body).toMatchObject({
  id: expect.any(String),
  email: payload.email,
  createdAt: expect.any(String),
});

// Assert headers when relevant
expect(response.headers['content-type']).toMatch(/application\/json/);
```

### Transactional test DB

```typescript
// Per-test transaction rollback for isolation
beforeEach(async () => {
  queryRunner = dataSource.createQueryRunner();
  await queryRunner.startTransaction();
});

afterEach(async () => {
  await queryRunner.rollbackTransaction();
  await queryRunner.release();
});
```

### Testing error responses

```typescript
// Don't just check status — verify the error shape
const response = await request(app.getHttpServer())
  .get('/users/nonexistent-id')
  .expect(404);

expect(response.body).toMatchObject({
  statusCode: 404,
  message: expect.any(String),
  // Should NOT contain stack traces or internal paths
});
expect(response.body.message).not.toMatch(/\/src\//);
expect(response.body.message).not.toMatch(/Error:/);
```

## Adversarial Patterns for NestJS

### DTO validation bypass

```typescript
// ValidationPipe with whitelist:true should strip unknown properties
const response = await request(app.getHttpServer())
  .post('/users')
  .send({ ...validPayload, isAdmin: true, __proto__: { admin: true } })
  .expect(201);

// Verify the extra fields did NOT persist
const user = await userRepo.findOne({ where: { id: response.body.id } });
expect(user.isAdmin).toBeFalsy();
```

### Authorization boundary tests

```typescript
// User A accessing User B's resource
const tokenA = await getToken(userA);
const response = await request(app.getHttpServer())
  .get(`/users/${userB.id}/settings`)
  .set('Authorization', `Bearer ${tokenA}`)
  .expect(403); // or 404 — don't reveal existence

// Expired token
const expiredToken = generateExpiredToken(userA);
await request(app.getHttpServer())
  .get('/protected')
  .set('Authorization', `Bearer ${expiredToken}`)
  .expect(401);
```

### Pipe/guard ordering

NestJS applies guards before pipes before interceptors. Test that auth rejection
happens BEFORE validation (don't leak validation errors to unauthenticated users):

```typescript
// Invalid body + no auth = should get 401, not 400
await request(app.getHttpServer())
  .post('/protected')
  .send({ invalid: 'payload' })
  // No auth header
  .expect(401);
```

### Exception filter coverage

```typescript
// Verify custom exception filters handle all expected error types
// and don't leak internal errors on unexpected ones
const unknownError = new Error('unexpected internal failure');
// Trigger via a mocked provider that throws
mockService.doThing.mockRejectedValue(unknownError);

const response = await request(app.getHttpServer())
  .get('/endpoint-using-service')
  .expect(500);

// Generic message, no stack trace
expect(response.body.message).toBe('Internal server error');
expect(JSON.stringify(response.body)).not.toContain('unexpected internal failure');
```

## Anti-Patterns to Catch

- Testing with `any` typed mocks that silently accept anything
- Supertest assertions on status only (no body/header checks)
- Tests that import and call the controller method directly (bypasses pipes/guards)
- Mocking the repository inside a service test AND the service inside a
  controller test — the integration gap is where bugs hide
- Not testing validation pipe behavior (assuming DTOs "just work")
