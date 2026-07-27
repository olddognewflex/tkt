# AWS CDK Testing Reference

Stack-specific patterns for adversarial testing of AWS CDK infrastructure code.

## Template Assertions

### Basic resource property assertions

```typescript
import { Template, Match } from 'aws-cdk-lib/assertions';

const template = Template.fromStack(stack);

// Verify a resource exists with specific properties
template.hasResourceProperties('AWS::Lambda::Function', {
  Runtime: 'nodejs20.x',
  Timeout: 30,
  MemorySize: 256,
  Environment: {
    Variables: {
      NODE_ENV: 'production',
    },
  },
});

// Verify resource COUNT (catches accidental duplicates or missing resources)
template.resourceCountIs('AWS::Lambda::Function', 3);
```

### Security-focused assertions

```typescript
// S3 bucket encryption
template.hasResourceProperties('AWS::S3::Bucket', {
  BucketEncryption: {
    ServerSideEncryptionConfiguration: [
      {
        ServerSideEncryptionByDefault: {
          SSEAlgorithm: 'aws:kms',
        },
      },
    ],
  },
  PublicAccessBlockConfiguration: {
    BlockPublicAcls: true,
    BlockPublicPolicy: true,
    IgnorePublicAcls: true,
    RestrictPublicBuckets: true,
  },
});

// No public S3 buckets
template.hasResourceProperties('AWS::S3::BucketPolicy', {
  PolicyDocument: Match.not(
    Match.objectLike({
      Statement: Match.arrayWith([
        Match.objectLike({
          Principal: '*',
          Effect: 'Allow',
        }),
      ]),
    })
  ),
});

// RDS encryption at rest
template.hasResourceProperties('AWS::RDS::DBInstance', {
  StorageEncrypted: true,
  DeletionProtection: true,
});

// Security group — no 0.0.0.0/0 ingress on sensitive ports
template.hasResourceProperties('AWS::EC2::SecurityGroup', {
  SecurityGroupIngress: Match.not(
    Match.arrayWith([
      Match.objectLike({
        CidrIp: '0.0.0.0/0',
        FromPort: 5432, // postgres
      }),
    ])
  ),
});
```

### IAM least-privilege assertions

```typescript
// Lambda role should NOT have wildcard actions
const roles = template.findResources('AWS::IAM::Role');
for (const [logicalId, role] of Object.entries(roles)) {
  const policies = role.Properties?.Policies ?? [];
  for (const policy of policies) {
    const statements = policy.PolicyDocument?.Statement ?? [];
    for (const stmt of statements) {
      if (stmt.Effect === 'Allow') {
        expect(stmt.Action).not.toContain('*');
        expect(stmt.Resource).not.toBe('*');
      }
    }
  }
}

// Verify no inline policies with admin access
template.hasResourceProperties('AWS::IAM::Role', {
  Policies: Match.not(
    Match.arrayWith([
      Match.objectLike({
        PolicyDocument: Match.objectLike({
          Statement: Match.arrayWith([
            Match.objectLike({
              Effect: 'Allow',
              Action: '*',
              Resource: '*',
            }),
          ]),
        }),
      }),
    ])
  ),
});
```

## Snapshot Tests — Acknowledge Limits

Snapshot tests are a **diff alarm**, not a test. They tell you "something changed"
— not whether the change is correct or incorrect.

```typescript
// Snapshot: useful as a change-detection alarm
// BUT: reviewing a 5000-line JSON diff is meaningless
// Use targeted assertions for actual correctness guarantees
test('stack matches snapshot', () => {
  const template = Template.fromStack(stack);
  expect(template.toJSON()).toMatchSnapshot();
});
```

**When snapshots are useful:**
- Catching unintended resource deletions during refactors
- Detecting drift from expected resource counts

**When snapshots are harmful:**
- As the sole test (proves nothing about correctness)
- When they get auto-updated without review (`-u` is dangerous)
- When the snapshot is too large to meaningfully review

## Aspects for Cross-Stack Policy

```typescript
import { Annotations, IAspect, Stack } from 'aws-cdk-lib';
import { CfnBucket } from 'aws-cdk-lib/aws-s3';

class BucketEncryptionAspect implements IAspect {
  visit(node: IConstruct): void {
    if (node instanceof CfnBucket) {
      if (!node.bucketEncryption) {
        Annotations.of(node).addError('All S3 buckets must have encryption enabled');
      }
    }
  }
}

// In test:
test('all buckets are encrypted', () => {
  Aspects.of(stack).add(new BucketEncryptionAspect());
  const annotations = Annotations.fromStack(stack);
  annotations.hasNoError('/MyStack/*', Match.anyValue());
});
```

## Adversarial Patterns for CDK

### Removal protection

```typescript
// DynamoDB tables should have deletion protection
template.hasResource('AWS::DynamoDB::Table', {
  DeletionPolicy: 'Retain',
  UpdateReplacePolicy: 'Retain',
});

// Critical resources should not be replaceable on update
template.hasResource('AWS::RDS::DBInstance', {
  UpdateReplacePolicy: Match.not('Delete'),
});
```

### Environment separation

```typescript
// Production stack should NOT reference dev account IDs
const templateJson = JSON.stringify(template.toJSON());
expect(templateJson).not.toContain('123456789012'); // dev account
expect(templateJson).not.toContain('dev-');

// Production stack should have higher resource limits
template.hasResourceProperties('AWS::Lambda::Function', {
  MemorySize: Match.not(128), // 128 = default = probably not tuned for prod
  Timeout: Match.not(3),      // 3s = default = probably not tuned for prod
});
```

### VPC / Network isolation

```typescript
// Lambda in VPC should have both private subnets
template.hasResourceProperties('AWS::Lambda::Function', {
  VpcConfig: {
    SubnetIds: Match.not(Match.arrayWith([])), // not empty
    SecurityGroupIds: Match.not(Match.arrayWith([])),
  },
});

// No resources in public subnets that shouldn't be
const publicSubnetRefs = findPublicSubnetRefs(template);
const lambdas = template.findResources('AWS::Lambda::Function');
for (const [id, lambda] of Object.entries(lambdas)) {
  const subnets = lambda.Properties?.VpcConfig?.SubnetIds ?? [];
  for (const subnet of subnets) {
    expect(publicSubnetRefs).not.toContainEqual(subnet);
  }
}
```

### Cost traps

```typescript
// NAT Gateways are expensive — verify they're intentional
const natCount = Object.keys(
  template.findResources('AWS::EC2::NatGateway')
).length;
expect(natCount).toBeLessThanOrEqual(expectedNatCount);

// On-demand instances when spot would suffice
template.hasResourceProperties('AWS::ECS::Service', {
  CapacityProviderStrategy: Match.arrayWith([
    Match.objectLike({
      CapacityProvider: Match.stringLikeRegexp('FARGATE_SPOT'),
    }),
  ]),
});
```

### Cross-stack reference integrity

```typescript
// When stacks export values, verify consumers actually use them
// (orphaned exports are tech debt and can't be removed without deploy coordination)
const exports = template.findOutputs('*');
for (const [name, output] of Object.entries(exports)) {
  if (output.Export) {
    // Document why this export exists
    expect(output.Description).toBeDefined();
  }
}
```

## Anti-Patterns to Catch

- Snapshot-only testing (no targeted assertions on security/permissions)
- Testing synthesized template but not the construct inputs (garbage-in verification)
- Missing `DeletionPolicy: Retain` on stateful resources (databases, buckets)
- Wildcard IAM permissions (`*` on Action or Resource)
- Hardcoded account IDs or region strings in constructs
- No assertions on security group rules (default allows all egress)
- Missing encryption on data-at-rest resources
- Lambda functions with default 3s timeout and 128MB memory in production
